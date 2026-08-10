"""Scheduled embedding indexer (SPEC §5.1b, §5.4 indexing half).

Orchestrates the pieces built so far: walk the repo tree, chunk by
function (chunker.py, reusing Phase 2's tree-sitter parsing), build a
simple name-based call graph (call_graph.py) and a has-tests flag from the
*whole* repo (caller/test-reference metadata needs full-repo context even
when only re-embedding a subset of changed files), attach curated
incident tags (incidents.py), embed (embedding.py), and upsert into
pgvector (db.py).
"""
import json
import re

from worker.diff_parser.languages import get_language_for_file
from worker.github_client import (
    fetch_file_content,
    get_changed_files_between,
    get_default_branch_head_sha,
    list_repo_tree,
)
from worker.retrieval.call_graph import build_callers_by_name, extract_call_edges
from worker.retrieval.chunker import chunk_file
from worker.retrieval.db import ensure_schema, get_connection, get_last_indexed_sha, set_last_indexed_sha
from worker.retrieval.embedding import embed_texts
from worker.retrieval.incidents import load_incident_tags


_TEST_FILENAME_RE = re.compile(r"^test_|_test\.|\.test\.", re.IGNORECASE)


def _is_test_file(path: str) -> bool:
    """A path is a test file if it lives under a test(s)/ directory, or its
    filename follows the test_*/*_test/*.test naming convention. Plain
    substring matching is deliberately avoided — it would wrongly classify
    e.g. "contest/foo.py" as a test file.
    """
    *dirs, filename = path.split("/")
    if any(re.fullmatch(r"tests?", d, re.IGNORECASE) for d in dirs):
        return True
    return bool(_TEST_FILENAME_RE.search(filename))


def _fetch_supported_files(owner: str, name: str, paths: list[str], ref: str) -> dict[str, str]:
    """Fetches every supported-language file's content. Deliberately does
    NOT swallow individual fetch failures (e.g. GitHub rate limiting): a
    partial fetch would silently produce incomplete call-graph/has_tests
    metadata while still letting the caller mark the repo "freshly
    indexed" (N5) — a stale-but-correct index is better than a
    fresh-looking-but-incomplete one. Let it raise; the scheduled job
    retries on its next tick.
    """
    return {path: fetch_file_content(owner, name, path, ref) for path in paths}


def _referenced_in_tests(all_files: dict[str, str]) -> set[str]:
    """Names referenced by a call expression somewhere inside a test file
    (SPEC §5.4: "whether tests reference this function"). Name-based, same
    limitation as the call graph itself.
    """
    referenced = set()
    for path, source in all_files.items():
        if not _is_test_file(path):
            continue
        for _caller, callee in extract_call_edges(path, source):
            referenced.add(callee)
    return referenced


def index_repo(
    repo: str,
    owner: str,
    name: str,
    database_url: str,
    embedding_model: str,
    token: str | None = None,
    force_full: bool = False,
) -> dict:
    """Index (or incrementally re-index) `repo` into pgvector. Returns a
    stats dict describing what happened — used by both the manual-trigger
    path and the scheduled job's logging.
    """
    conn = get_connection(database_url)
    ensure_schema(conn)

    head_sha = get_default_branch_head_sha(owner, name)
    last_sha = get_last_indexed_sha(conn, repo)

    if last_sha == head_sha and not force_full:
        return {"status": "up_to_date", "sha": head_sha, "chunks_indexed": 0}

    all_paths = list_repo_tree(owner, name, head_sha)
    supported_paths = [p for p in all_paths if get_language_for_file(p) is not None]

    if last_sha is None or force_full:
        changed_paths = supported_paths
        mode = "full"
    else:
        diff_files = get_changed_files_between(owner, name, last_sha, head_sha)
        changed_paths = [
            f["filename"]
            for f in diff_files
            if f["status"] != "removed" and get_language_for_file(f["filename"]) is not None
        ]
        removed_paths = [
            f["filename"]
            for f in diff_files
            if f["status"] == "removed" and get_language_for_file(f["filename"]) is not None
        ]
        mode = "incremental"

    # Call-graph/test-reference metadata needs full-repo context regardless
    # of which files changed — a caller in an untouched file is still a
    # real caller. Only chunking+embedding is scoped to changed_paths.
    all_files = _fetch_supported_files(owner, name, supported_paths, head_sha)
    callers_by_name = build_callers_by_name(all_files)
    test_referenced = _referenced_in_tests(all_files)
    incident_tags = load_incident_tags()

    chunks = []
    for path in changed_paths:
        source = all_files.get(path)
        if source is None:
            continue
        chunks.extend(chunk_file(path, source))

    if chunks:
        embeddings = embed_texts([c.code for c in chunks], embedding_model)
    else:
        embeddings = []

    # Single transaction: deletes + upserts + the last-indexed-SHA bump all
    # succeed together or none do. Without this, a failure partway through
    # the write loop could leave last_indexed_sha pointing past chunks that
    # were never actually written.
    with conn.transaction(), conn.cursor() as cur:
        changed_set = set(changed_paths)
        if mode == "incremental":
            changed_set |= set(removed_paths)
        for path in changed_set:
            cur.execute(
                "DELETE FROM code_chunks WHERE repo = %s AND file_path = %s", (repo, path)
            )
        for chunk, embedding in zip(chunks, embeddings):
            tags = incident_tags.get((chunk.file_path, chunk.function_name), [])
            cur.execute(
                """
                INSERT INTO code_chunks
                    (repo, file_path, function_name, language, start_line, end_line,
                     code, embedding, callers, has_tests, incident_tags, commit_sha)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (repo, file_path, function_name, start_line) DO UPDATE SET
                    end_line = EXCLUDED.end_line,
                    code = EXCLUDED.code,
                    embedding = EXCLUDED.embedding,
                    callers = EXCLUDED.callers,
                    has_tests = EXCLUDED.has_tests,
                    incident_tags = EXCLUDED.incident_tags,
                    commit_sha = EXCLUDED.commit_sha,
                    updated_at = now()
                """,
                (
                    repo,
                    chunk.file_path,
                    chunk.function_name,
                    chunk.language,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.code,
                    embedding,
                    json.dumps(callers_by_name.get(chunk.function_name, [])),
                    chunk.function_name in test_referenced,
                    json.dumps(tags),
                    head_sha,
                ),
            )
        set_last_indexed_sha(conn, repo, head_sha)

    conn.close()

    return {
        "status": "indexed",
        "mode": mode,
        "sha": head_sha,
        "previous_sha": last_sha,
        "files_processed": len(changed_paths),
        "chunks_indexed": len(chunks),
    }
