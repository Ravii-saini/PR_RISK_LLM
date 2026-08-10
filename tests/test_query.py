import json

from tests.conftest import TEST_REPO
from worker.retrieval.embedding import embed_text
from worker.retrieval.query import retrieve_context_for_function

MODEL = "jinaai/jina-embeddings-v2-base-code"

ADD_CODE = "def add(a, b):\n    return a + b\n"
SUM_CODE = "def sum_two(x, y):\n    return x + y\n"  # semantically near-identical to add
UNRELATED_CODE = "def render_html_page(title, body):\n    return f'<html><h1>{title}</h1>{body}</html>'\n"


def _insert_chunk(pg_conn, file_path, function_name, code, callers=None, has_tests=False, incident_tags=None):
    embedding = embed_text(code, MODEL)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO code_chunks
                (repo, file_path, function_name, language, start_line, end_line,
                 code, embedding, callers, has_tests, incident_tags, commit_sha)
            VALUES (%s, %s, %s, 'python', 1, 2, %s, %s, %s, %s, %s, 'deadbeef')
            """,
            (
                TEST_REPO,
                file_path,
                function_name,
                code,
                embedding,
                json.dumps(callers or []),
                has_tests,
                json.dumps(incident_tags or []),
            ),
        )


def test_retrieve_returns_own_metadata_for_indexed_function(pg_conn, settings):
    _insert_chunk(
        pg_conn,
        "a.py",
        "add",
        ADD_CODE,
        callers=["b.py::caller"],
        has_tests=True,
        incident_tags=[{"tag": "BUG-1", "summary": "x", "reference": "https://github.com/x/y/pull/1"}],
    )

    result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "add", ADD_CODE, top_k=5)

    assert result.already_indexed is True
    assert result.callers == ["b.py::caller"]
    assert result.has_tests is True
    assert result.incident_tags[0]["tag"] == "BUG-1"


def test_retrieve_for_unindexed_function_returns_empty_own_metadata(pg_conn, settings):
    result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "nope", ADD_CODE, top_k=5)
    assert result.already_indexed is False
    assert result.callers == []
    assert result.has_tests is False
    assert result.incident_tags == []


def test_retrieve_similar_ranks_semantically_close_code_higher(pg_conn, settings):
    _insert_chunk(pg_conn, "a.py", "add", ADD_CODE)
    _insert_chunk(pg_conn, "a.py", "sum_two", SUM_CODE)
    _insert_chunk(pg_conn, "a.py", "render_html_page", UNRELATED_CODE)

    result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "add", ADD_CODE, top_k=5)

    names = [s.function_name for s in result.similar]
    assert names[0] == "sum_two"  # near-identical arithmetic function ranks first
    assert names[-1] == "render_html_page"  # unrelated HTML-rendering function ranks last
    assert result.similar[0].similarity > result.similar[-1].similarity


def test_retrieve_excludes_the_queried_function_itself(pg_conn, settings):
    _insert_chunk(pg_conn, "a.py", "add", ADD_CODE)
    _insert_chunk(pg_conn, "a.py", "sum_two", SUM_CODE)

    result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "add", ADD_CODE, top_k=5)

    assert all(
        not (s.file_path == "a.py" and s.function_name == "add") for s in result.similar
    )


def test_retrieve_respects_top_k(pg_conn, settings):
    for i in range(4):
        _insert_chunk(pg_conn, "a.py", f"fn{i}", f"def fn{i}(x):\n    return x + {i}\n")

    result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "add", ADD_CODE, top_k=2)
    assert len(result.similar) == 2


def test_retrieve_scoped_to_repo(pg_conn, settings):
    _insert_chunk(pg_conn, "a.py", "add", ADD_CODE)
    other_repo = "other-org/other-repo"
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO code_chunks
                (repo, file_path, function_name, language, start_line, end_line,
                 code, embedding, callers, has_tests, incident_tags, commit_sha)
            VALUES (%s, 'a.py', 'sum_two', 'python', 1, 2, %s, %s, '[]', false, '[]', 'deadbeef')
            """,
            (other_repo, SUM_CODE, embed_text(SUM_CODE, MODEL)),
        )
    try:
        # sum_two is near-identical to add and would rank #1 if repo
        # scoping leaked across repos — asserting it's absent proves the
        # WHERE repo = %s clause actually excludes the other repo's rows.
        result = retrieve_context_for_function(pg_conn, TEST_REPO, MODEL, "a.py", "add", ADD_CODE, top_k=5)
        assert all(s.function_name != "sum_two" for s in result.similar)
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repo = %s", (other_repo,))
