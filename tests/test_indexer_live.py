"""Live integration test against the real pinned eval repo (psf/requests) —
network-marked and excluded from the default run for the same reason as
Phase 2's live diff-parser tests (see pyproject.toml, PROBLEMS.md): repeated
runs exhaust GitHub's 60/hr unauthenticated limit. Run explicitly with
`uv run pytest -m network`.
"""
import pytest

from tests.conftest import TEST_REPO
from worker.retrieval.db import get_last_indexed_sha
from worker.retrieval.embedding import embed_texts
from worker.retrieval.incidents import load_incident_tags
from worker.retrieval.indexer import index_repo

pytestmark = pytest.mark.network

EVAL_OWNER, EVAL_NAME = "psf", "requests"
EVAL_REPO = f"{EVAL_OWNER}/{EVAL_NAME}"


@pytest.fixture
def eval_repo_conn(settings):
    from worker.retrieval.db import ensure_schema, get_connection

    conn = get_connection(settings.database_url)
    ensure_schema(conn)
    yield conn
    conn.close()


def test_full_index_of_eval_repo_populates_chunks_with_metadata(settings, eval_repo_conn):
    result = index_repo(
        repo=EVAL_REPO,
        owner=EVAL_OWNER,
        name=EVAL_NAME,
        database_url=settings.database_url,
        embedding_model=settings.embedding_model,
        force_full=True,
    )
    assert result["status"] == "indexed"
    assert result["mode"] == "full"
    assert result["chunks_indexed"] > 0

    stored_sha = get_last_indexed_sha(eval_repo_conn, EVAL_REPO)
    assert stored_sha == result["sha"]

    # At least one curated incident-tagged function must have actually been
    # indexed with its tag attached — proves incident_tags.json's function
    # names still match the real current source, not stale references.
    incident_tags = load_incident_tags()
    (file_path, function_name), _ = next(iter(incident_tags.items()))
    with eval_repo_conn.cursor() as cur:
        cur.execute(
            "SELECT incident_tags FROM code_chunks WHERE repo = %s AND file_path = %s AND function_name = %s",
            (EVAL_REPO, file_path, function_name),
        )
        row = cur.fetchone()
    assert row is not None, f"{function_name} in {file_path} was not indexed — incident tag is stale"
    assert row[0], "chunk was indexed but incident_tags was not attached"


def test_second_run_with_no_new_commits_is_up_to_date(settings):
    result = index_repo(
        repo=EVAL_REPO,
        owner=EVAL_OWNER,
        name=EVAL_NAME,
        database_url=settings.database_url,
        embedding_model=settings.embedding_model,
        force_full=False,
    )
    assert result["status"] == "up_to_date"
    assert result["chunks_indexed"] == 0
