"""Java diff parser tests (SPEC §5.3).

Unlike Python and TypeScript, Java's `method_declaration` node's own range
already starts at its first annotation (`@Override`, `@Deprecated`, ...) —
no wrapper or sibling-widening logic needed here. Still worth a dedicated
regression test: this is exactly the kind of assumption that's easy to get
wrong by analogy with Python/TS instead of checking the actual grammar.
"""
from worker.diff_parser.languages import JAVA
from worker.diff_parser.parser import changed_functions, find_function_ranges

SOURCE = """public class Foo {
    @Override
    public int method(
        int a,
        int b
    ) {
        return a + b;
    }

    @Deprecated
    @SuppressWarnings("unchecked")
    public void other() {
        return;
    }
}
"""


def test_find_function_ranges_already_includes_annotations():
    functions = find_function_ranges(SOURCE, JAVA)
    by_name = {fn["name"]: fn for fn in functions}

    assert set(by_name) == {"method", "other"}
    assert by_name["method"] == {"name": "method", "start_line": 2, "end_line": 8}
    # Two stacked annotations: range must include both, starting at the first.
    assert by_name["other"] == {"name": "other", "start_line": 10, "end_line": 14}


def test_changed_functions_flags_annotation_only_change():
    patch = '@@ -10,1 +10,1 @@\n-    @Deprecated\n+    @Deprecated(since="2.0")\n'
    result = changed_functions(SOURCE, patch, JAVA)
    assert [fn["name"] for fn in result] == ["other"]


def test_changed_functions_flags_change_inside_multiline_signature():
    patch = "@@ -4,1 +4,1 @@\n-        int b\n+        long b\n"
    result = changed_functions(SOURCE, patch, JAVA)
    assert [fn["name"] for fn in result] == ["method"]
