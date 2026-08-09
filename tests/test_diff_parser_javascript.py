"""JavaScript diff parser tests (SPEC §5.3).

Covers the case a naive detector misses: `arrow_function` has no name of
its own in the grammar — the name has to be recovered from how it's bound
(`const x = () => {}`, a class field, or an object literal key).
"""
from worker.diff_parser.languages import JAVASCRIPT
from worker.diff_parser.parser import changed_functions, find_function_ranges

SOURCE = """function regular(a, b) {
  return a + b;
}

const arrow = (x, y) => {
  return x - y;
};

class Foo {
  @dec
  method(
    a,
    b
  ) {
    return a * b;
  }

  arrowProp = () => {
    return 1;
  };
}
"""


def test_find_function_ranges_names_all_shapes_correctly():
    functions = find_function_ranges(SOURCE, JAVASCRIPT)
    by_name = {fn["name"]: fn for fn in functions}

    assert set(by_name) == {"regular", "arrow", "method", "arrowProp"}
    # Named function declaration.
    assert by_name["regular"] == {"name": "regular", "start_line": 1, "end_line": 3}
    # Arrow function assigned to a const — name recovered from the
    # variable_declarator, since arrow_function itself has none.
    assert by_name["arrow"]["start_line"] == 5
    # Decorator is a *child* of method_definition in the JS grammar (unlike
    # TypeScript, where it's a sibling) — the range already starts there.
    assert by_name["method"]["start_line"] == 10
    assert by_name["method"]["end_line"] == 16
    # Arrow function assigned to a class field — name recovered from the
    # field_definition's "property".
    assert by_name["arrowProp"]["start_line"] == 18


def test_changed_functions_flags_change_inside_multiline_signature():
    # Change on line 12 (inside method's multi-line signature), not on the
    # opening line — must still be attributed to `method`.
    patch = "@@ -12,1 +12,1 @@\n-    b\n+    b,  # changed\n"
    result = changed_functions(SOURCE, patch, JAVASCRIPT)
    assert [fn["name"] for fn in result] == ["method"]


def test_changed_functions_flags_decorator_only_change():
    patch = "@@ -10,1 +10,1 @@\n-  @dec\n+  @dec2\n"
    result = changed_functions(SOURCE, patch, JAVASCRIPT)
    assert [fn["name"] for fn in result] == ["method"]


def test_generator_function_declaration_is_detected():
    # `function* gen() {}` is a distinct grammar node from
    # `function_declaration` — found completely missing (not misattributed,
    # just silently invisible) during adversarial testing.
    source = "function* gen() {\n  yield 1;\n}\n"
    functions = find_function_ranges(source, JAVASCRIPT)
    assert [fn["name"] for fn in functions] == ["gen"]
