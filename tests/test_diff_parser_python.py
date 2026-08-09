"""Python diff parser tests (SPEC §5.3, F2).

Deliberately includes cases a regex-based function detector breaks on:
a multi-line function signature where the change lands mid-signature (not
on the `def` line itself), a decorator, and a nested def — tree-sitter
handles all of these correctly because it parses a real syntax tree instead
of matching line patterns.

`parse_patch_changed_lines` is language-agnostic (it only reads unified-diff
syntax), so its tests live here rather than being duplicated per language.
"""
from worker.diff_parser.languages import PYTHON
from worker.diff_parser.parser import (
    changed_functions,
    find_function_ranges,
    parse_patch_changed_lines,
)

SIMPLE_PATCH = """@@ -1,4 +1,5 @@
 line1
-line2
+line2modified
+line2b
 line3
 line4
"""

TRICKY_SOURCE = '''import os


@decorator
def top_level(x, y):
    return x + y


class Foo:
    @staticmethod
    def method_one(
        a,
        b,
    ):
        return a - b

    async def method_two(self):
        def nested():
            return 1
        return nested()
'''


def test_parse_patch_changed_lines_tracks_new_file_line_numbers():
    changed = parse_patch_changed_lines(SIMPLE_PATCH)
    assert changed == {2, 3}


def test_parse_patch_changed_lines_multiple_hunks():
    patch = (
        "@@ -1,2 +1,2 @@\n"
        " a\n"
        "-b\n"
        "+b2\n"
        "@@ -10,2 +10,3 @@\n"
        " x\n"
        "+y\n"
        " z\n"
    )
    changed = parse_patch_changed_lines(patch)
    assert changed == {2, 11}


def test_find_function_ranges_handles_decorator_nested_and_multiline_signature():
    functions = find_function_ranges(TRICKY_SOURCE, PYTHON)
    by_name = {fn["name"]: fn for fn in functions}

    assert set(by_name) == {"top_level", "method_one", "method_two", "nested"}
    # Decorated function: range starts at the @decorator line, not `def` —
    # a decorator-only change (route path, retry policy, permission check)
    # must still count as a change to the function it decorates.
    assert by_name["top_level"] == {"name": "top_level", "start_line": 4, "end_line": 6}
    # Multi-line signature: the range must span the whole signature + body,
    # not just the `def` line — a naive single-line regex can't determine this.
    assert by_name["method_one"] == {"name": "method_one", "start_line": 10, "end_line": 15}
    assert by_name["method_two"] == {"name": "method_two", "start_line": 17, "end_line": 20}
    assert by_name["nested"] == {"name": "nested", "start_line": 18, "end_line": 19}


def test_changed_functions_flags_decorator_only_change():
    # A change to only the @decorator line (e.g. a route path or a retry
    # policy) must still be attributed to the function it decorates — this
    # was found broken during adversarial testing and then fixed.
    source = '@app.route("/old")\ndef view():\n    return 1\n'
    patch = '@@ -1,1 +1,1 @@\n-@app.route("/old")\n+@app.route("/new")\n'
    result = changed_functions(source, patch, PYTHON)
    assert [fn["name"] for fn in result] == ["view"]


def test_changed_functions_flags_only_functions_touched_by_the_diff():
    # A change on line 12 (inside method_one's multi-line signature, not on
    # its `def` line) must still flag method_one — this is exactly the case
    # a line-pattern-based detector would miss.
    patch = "@@ -12,1 +12,1 @@\n-        a,\n+        a,  # changed\n"
    result = changed_functions(TRICKY_SOURCE, patch, PYTHON)
    assert [fn["name"] for fn in result] == ["method_one"]


def test_changed_functions_no_overlap_returns_empty():
    # A change to the module-level import (line 1) touches no function body.
    patch = "@@ -1,1 +1,1 @@\n-import os\n+import os, sys\n"
    result = changed_functions(TRICKY_SOURCE, patch, PYTHON)
    assert result == []


def test_changed_functions_handles_brand_new_file():
    # A newly-added file's patch has a `-0,0` hunk (no old-side lines) and
    # every line on the '+' side — must not off-by-one or crash on this shape.
    source = "def new_func():\n    return 42\n"
    patch = "@@ -0,0 +1,2 @@\n+def new_func():\n+    return 42\n"
    result = changed_functions(source, patch, PYTHON)
    assert [fn["name"] for fn in result] == ["new_func"]
