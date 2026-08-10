from tests.conftest import TEST_REPO
from worker.retrieval.db import ensure_schema, get_last_indexed_sha, set_last_indexed_sha


def test_ensure_schema_is_idempotent(pg_conn):
    ensure_schema(pg_conn)
    ensure_schema(pg_conn)  # second call must not raise


def test_last_indexed_sha_round_trip(pg_conn):
    assert get_last_indexed_sha(pg_conn, TEST_REPO) is None
    set_last_indexed_sha(pg_conn, TEST_REPO, "sha-1")
    assert get_last_indexed_sha(pg_conn, TEST_REPO) == "sha-1"


def test_last_indexed_sha_upsert_overwrites(pg_conn):
    set_last_indexed_sha(pg_conn, TEST_REPO, "sha-1")
    set_last_indexed_sha(pg_conn, TEST_REPO, "sha-2")
    assert get_last_indexed_sha(pg_conn, TEST_REPO) == "sha-2"


def test_code_chunks_insert_and_vector_dimension(pg_conn):
    embedding = [0.1] * 768
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO code_chunks
                (repo, file_path, function_name, language, start_line, end_line,
                 code, embedding, callers, has_tests, incident_tags, commit_sha)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                TEST_REPO,
                "a.py",
                "foo",
                "python",
                1,
                2,
                "def foo():\n    pass",
                embedding,
                "[]",
                False,
                "[]",
                "deadbeef",
            ),
        )
        cur.execute(
            "SELECT function_name, has_tests FROM code_chunks WHERE repo = %s", (TEST_REPO,)
        )
        row = cur.fetchone()
    assert row == ("foo", False)


def test_code_chunks_unique_constraint_upserts_on_conflict(pg_conn):
    embedding = [0.1] * 768
    insert_sql = """
        INSERT INTO code_chunks
            (repo, file_path, function_name, language, start_line, end_line,
             code, embedding, callers, has_tests, incident_tags, commit_sha)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (repo, file_path, function_name, start_line) DO UPDATE SET
            code = EXCLUDED.code, commit_sha = EXCLUDED.commit_sha
    """
    args = [TEST_REPO, "a.py", "foo", "python", 1, 2, "v1", embedding, "[]", False, "[]", "sha1"]
    with pg_conn.cursor() as cur:
        cur.execute(insert_sql, args)
        args[6] = "v2"
        args[-1] = "sha2"
        cur.execute(insert_sql, args)
        cur.execute(
            "SELECT code, commit_sha FROM code_chunks WHERE repo = %s", (TEST_REPO,)
        )
        rows = cur.fetchall()
    assert rows == [("v2", "sha2")]
