"""Go diff parser tests (SPEC §5.3).

Go has no decorator/annotation syntax, so no range-widening is needed here —
this mainly proves the registry correctly distinguishes top-level functions
(`function_declaration`) from methods with a receiver (`method_declaration`),
and handles a multi-line signature correctly.
"""
from worker.diff_parser.languages import GO
from worker.diff_parser.parser import changed_functions, find_function_ranges

SOURCE = """package main

func Regular(a, b int) int {
\treturn a + b
}

type Foo struct{}

func (f *Foo) Method(
\ta int,
\tb int,
) int {
\treturn a * b
}
"""


def test_find_function_ranges_distinguishes_function_and_method():
    functions = find_function_ranges(SOURCE, GO)
    by_name = {fn["name"]: fn for fn in functions}

    assert set(by_name) == {"Regular", "Method"}
    assert by_name["Regular"] == {"name": "Regular", "start_line": 3, "end_line": 5}
    assert by_name["Method"] == {"name": "Method", "start_line": 9, "end_line": 14}


def test_changed_functions_flags_change_inside_multiline_signature():
    # Change on line 10 (inside Method's multi-line receiver signature).
    patch = "@@ -10,1 +10,1 @@\n-\ta int,\n+\ta int64,\n"
    result = changed_functions(SOURCE, patch, GO)
    assert [fn["name"] for fn in result] == ["Method"]


def test_changed_functions_no_overlap_returns_empty():
    patch = "@@ -1,1 +1,1 @@\n-package main\n+package main // comment\n"
    result = changed_functions(SOURCE, patch, GO)
    assert result == []
