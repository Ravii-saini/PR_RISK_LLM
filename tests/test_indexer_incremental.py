"""Exercises the incremental-reindex branch of index_repo (SPEC §5.1b:
"diffs it against the last-indexed commit SHA... re-embeds only changed
functions") without hitting the real GitHub API — the file-fetching and
tree/diff functions are monkeypatched, but embedding and Postgres writes
are real (same trust level as the rest of this test suite's DB tests).
"""
import worker.retrieval.indexer as indexer
from tests.conftest import TEST_REPO

V1_FILES = {
    "a.py": "def alpha():\n    return 1\n",
    "b.py": "def beta():\n    return 2\n",
}


def _patch_repo_state(monkeypatch, head_sha, tree_paths, files, changed_files=None):
    monkeypatch.setattr(indexer, "get_default_branch_head_sha", lambda owner, name: head_sha)
    monkeypatch.setattr(indexer, "list_repo_tree", lambda owner, name, sha: tree_paths)
    monkeypatch.setattr(
        indexer,
        "fetch_file_content",
        lambda owner, name, path, ref: files[path],
    )
    if changed_files is not None:
        monkeypatch.setattr(
            indexer, "get_changed_files_between", lambda owner, name, base, head: changed_files
        )


def test_full_index_then_incremental_update_and_removal(monkeypatch, pg_conn, settings):
    # --- first run: full index of a.py + b.py ---
    _patch_repo_state(monkeypatch, "sha-v1", ["a.py", "b.py"], V1_FILES)
    result = indexer.index_repo(
        repo=TEST_REPO,
        owner="test-org",
        name="does-not-exist",
        database_url=settings.database_url,
        embedding_model=settings.embedding_model,
    )
    assert result == {
        "status": "indexed",
        "mode": "full",
        "sha": "sha-v1",
        "previous_sha": None,
        "files_processed": 2,
        "chunks_indexed": 2,
    }

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT file_path, function_name FROM code_chunks WHERE repo = %s ORDER BY file_path",
            (TEST_REPO,),
        )
        assert cur.fetchall() == [("a.py", "alpha"), ("b.py", "beta")]

    # --- second run: a.py modified (function body changed), b.py removed ---
    v2_files = {"a.py": "def alpha():\n    return 999\n"}
    _patch_repo_state(
        monkeypatch,
        "sha-v2",
        ["a.py"],
        v2_files,
        changed_files=[
            {"filename": "a.py", "status": "modified"},
            {"filename": "b.py", "status": "removed"},
        ],
    )
    result = indexer.index_repo(
        repo=TEST_REPO,
        owner="test-org",
        name="does-not-exist",
        database_url=settings.database_url,
        embedding_model=settings.embedding_model,
    )
    assert result["mode"] == "incremental"
    assert result["previous_sha"] == "sha-v1"
    assert result["sha"] == "sha-v2"
    assert result["chunks_indexed"] == 1

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT file_path, function_name, code, commit_sha FROM code_chunks WHERE repo = %s",
            (TEST_REPO,),
        )
        rows = cur.fetchall()

    # b.py's chunk was deleted (removed file), a.py's chunk was updated in place
    assert len(rows) == 1
    file_path, function_name, code, commit_sha = rows[0]
    assert file_path == "a.py"
    assert function_name == "alpha"
    assert "999" in code
    assert commit_sha == "sha-v2"
