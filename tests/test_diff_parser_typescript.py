"""TypeScript diff parser tests (SPEC §5.3).

Unlike Python (decorator wrapped in a parent `decorated_definition`) and
unlike plain JS (decorator embedded as a child of `method_definition`),
TypeScript's grammar places `decorator` nodes as *preceding siblings* of
the method/class they decorate — a third distinct tree shape, needing its
own widening logic (`_ts_range` in languages.py).

Also covers a real bug found while building this fixture: TS's class-field
node is `public_field_definition` (not JS's `field_definition`) and uses a
different field name for the property (`"name"`, not `"property"`) — an
arrow function assigned to a class field was coming back `<anonymous>`
until this was fixed.
"""
from worker.diff_parser.languages import TYPESCRIPT
from worker.diff_parser.parser import changed_functions, find_function_ranges

SOURCE = """function regular(a: number, b: number): number {
  return a + b;
}

class Foo {
  @Input()
  @Deprecated()
  method(
    a: number,
    b: number
  ): number {
    return a * b;
  }

  arrowProp = (x: number): number => {
    return x + 1;
  };
}

const standalone = (x: number): number => x + 1;
"""


def test_find_function_ranges_names_and_widens_correctly():
    functions = find_function_ranges(SOURCE, TYPESCRIPT)
    by_name = {fn["name"]: fn for fn in functions}

    assert set(by_name) == {"regular", "method", "arrowProp", "standalone"}
    assert by_name["regular"] == {"name": "regular", "start_line": 1, "end_line": 3}
    # Two stacked decorators: range must widen to the *first* one (line 6),
    # not just the immediately-preceding one (line 7) or `method(` (line 8).
    assert by_name["method"] == {"name": "method", "start_line": 6, "end_line": 13}
    # Arrow function on a class field — name recovered via public_field_definition's
    # "name" field (not JS's "property" field — a real naming bug found and fixed here).
    assert by_name["arrowProp"] == {"name": "arrowProp", "start_line": 15, "end_line": 17}
    assert by_name["standalone"]["start_line"] == 20


def test_changed_functions_flags_change_to_only_the_first_of_two_decorators():
    # A change to @Input() (line 6) — the outer/first decorator, not the one
    # immediately above `method(` — must still be attributed to `method`.
    patch = "@@ -6,1 +6,1 @@\n-  @Input()\n+  @Input({required: true})\n"
    result = changed_functions(SOURCE, patch, TYPESCRIPT)
    assert [fn["name"] for fn in result] == ["method"]


def test_changed_functions_flags_change_inside_multiline_signature():
    patch = "@@ -9,1 +9,1 @@\n-    a: number,\n+    a: number,  // changed\n"
    result = changed_functions(SOURCE, patch, TYPESCRIPT)
    assert [fn["name"] for fn in result] == ["method"]


def test_generator_function_declaration_is_detected():
    # Same distinct-node-type gap as plain JS (shared grammar ancestry).
    source = "function* gen(): Generator<number> {\n  yield 1;\n}\n"
    functions = find_function_ranges(source, TYPESCRIPT)
    assert [fn["name"] for fn in functions] == ["gen"]
