from worker.retrieval.call_graph import build_callers_by_name, extract_call_edges

PY_SOURCE = """def helper(x):
    return x + 1

def caller_one(y):
    return helper(y) + 1

class Foo:
    def method(self, z):
        return helper(z)
"""

JS_SOURCE = """function helper(x) { return x + 1; }
function caller(y) { return helper(y); }
class Foo {
  method(z) { return this.helper(z); }
}
"""

GO_SOURCE = """package main

func helper(x int) int { return x + 1 }

func caller(y int) int {
	return helper(y)
}
"""

JAVA_SOURCE = """class Foo {
    int helper(int x) { return x + 1; }
    int caller(int y) { return helper(y); }
}
"""


def test_extract_call_edges_python():
    edges = extract_call_edges("a.py", PY_SOURCE)
    assert ("caller_one", "helper") in edges
    assert ("method", "helper") in edges


def test_extract_call_edges_module_level_call_has_no_caller():
    source = "def helper():\n    return 1\n\nhelper()\n"
    edges = extract_call_edges("a.py", source)
    assert edges == []  # top-level call isn't inside any function


def test_extract_call_edges_javascript():
    assert ("caller", "helper") in extract_call_edges("a.js", JS_SOURCE)
    assert ("method", "helper") in extract_call_edges("a.js", JS_SOURCE)


def test_extract_call_edges_go():
    assert ("caller", "helper") in extract_call_edges("a.go", GO_SOURCE)


def test_extract_call_edges_java():
    assert ("caller", "helper") in extract_call_edges("Foo.java", JAVA_SOURCE)


def test_extract_call_edges_unsupported_extension_returns_empty():
    assert extract_call_edges("README.md", "text") == []


def test_build_callers_by_name_qualifies_and_dedupes_across_files():
    files = {"a.py": PY_SOURCE, "b.py": "def other():\n    return helper(5)\n"}
    result = build_callers_by_name(files)
    assert result["helper"] == sorted(
        ["a.py::caller_one", "a.py::method", "b.py::other"]
    )
