from worker.retrieval.indexer import _is_test_file, _referenced_in_tests


def test_is_test_file_matches_test_directory():
    assert _is_test_file("tests/test_utils.py")
    assert _is_test_file("test/foo.js")


def test_is_test_file_does_not_match_non_test_paths():
    assert not _is_test_file("src/requests/utils.py")
    assert not _is_test_file("contest/foo.py")  # substring, not path segment


def test_referenced_in_tests_finds_calls_from_test_files_only():
    files = {
        "src/mod.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        "tests/test_mod.py": "from src.mod import helper\n\ndef test_helper():\n    assert helper() == 1\n",
    }
    referenced = _referenced_in_tests(files)
    assert "helper" in referenced
    assert "caller" not in referenced  # only called from non-test code
