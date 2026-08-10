from worker.retrieval.chunker import chunk_file, chunk_files

PY_SOURCE = '''import os

def add(a, b):
    """Add two numbers."""
    return a + b


class Foo:
    def bar(self, x):
        return x * 2
'''

JS_SOURCE = """function greet(name) {
  return `hi ${name}`;
}
"""


def test_chunk_file_python_finds_both_functions():
    chunks = chunk_file("mymodule.py", PY_SOURCE)
    names = {c.function_name for c in chunks}
    assert names == {"add", "bar"}


def test_chunk_file_ranges_are_exact():
    chunks = {c.function_name: c for c in chunk_file("mymodule.py", PY_SOURCE)}
    add = chunks["add"]
    assert add.start_line == 3
    assert add.end_line == 5
    assert add.code.startswith("def add(a, b):")
    assert '"""Add two numbers."""' in add.code


def test_chunk_file_records_language():
    chunks = chunk_file("mymodule.py", PY_SOURCE)
    assert all(c.language == "python" for c in chunks)
    assert all(c.file_path == "mymodule.py" for c in chunks)


def test_chunk_file_js():
    chunks = chunk_file("greet.js", JS_SOURCE)
    assert len(chunks) == 1
    assert chunks[0].function_name == "greet"
    assert chunks[0].language == "javascript"


def test_chunk_file_unsupported_extension_returns_empty():
    assert chunk_file("README.md", "not code") == []


def test_chunk_file_no_functions_returns_empty():
    assert chunk_file("empty.py", "x = 1\ny = 2\n") == []


def test_chunk_files_aggregates_across_supported_files_only():
    files = {"a.py": PY_SOURCE, "b.js": JS_SOURCE, "c.txt": "ignored"}
    chunks = chunk_files(files)
    names = {c.function_name for c in chunks}
    assert names == {"add", "bar", "greet"}
