"""Per-PR retrieval logic (SPEC §5.4 retrieval half): for a changed
function, query pgvector for (a) functions that call it, (b) semantically
similar past changes, and (c) any tagged incident history — combining
vector similarity with static call-graph/incident metadata rather than
relying on nearest-neighbor similarity alone.

This is deliberately structured so the "own" structural facts (callers,
test coverage, incident tags already attached to *this exact* function)
are always present in the result regardless of what the similarity search
finds — SPEC's point that "two functions can be semantically similar but
structurally unrelated, or structurally critical but semantically
unremarkable" means neither signal can substitute for the other.
"""
from dataclasses import dataclass, field

from worker.diff_parser.languages import get_language_for_file
from worker.diff_parser.parser import changed_functions
from worker.github_client import fetch_file_content, fetch_pr_files
from worker.retrieval.chunker import chunk_file
from worker.retrieval.db import get_connection
from worker.retrieval.embedding import embed_text


@dataclass(frozen=True)
class SimilarFunction:
    file_path: str
    function_name: str
    similarity: float
    callers: list[str]
    has_tests: bool
    incident_tags: list[dict]


@dataclass(frozen=True)
class RetrievalResult:
    file_path: str
    function_name: str
    already_indexed: bool
    callers: list[str] = field(default_factory=list)
    has_tests: bool = False
    incident_tags: list[dict] = field(default_factory=list)
    similar: list[SimilarFunction] = field(default_factory=list)
    code: str = ""


def retrieve_context_for_function(
    conn,
    repo: str,
    embedding_model: str,
    file_path: str,
    function_name: str,
    code: str,
    top_k: int = 5,
) -> RetrievalResult:
    """Retrieve grounding context for one changed function.

    `code` is the function's post-change body (from the diff being
    reviewed) — embedded fresh rather than reusing a stored embedding,
    since the whole point is to assess the *new* version of the code.

    The "own" lookup tries an exact `file_path` match first, then falls
    back to a path-boundary suffix match (found via the Phase 6 eval: a
    historical PR's file path can lack a prefix the current index has,
    e.g. after a repo migrates to a `src/` layout — `requests/utils.py`
    vs `src/requests/utils.py` — same function, same file, different
    recorded path). The suffix match requires a `/` (or nothing) right
    before the match point, so `myrequests/utils.py` does not incorrectly
    match a query for `requests/utils.py`.
    """
    own = conn.execute(
        """
        SELECT callers, has_tests, incident_tags
        FROM code_chunks
        WHERE repo = %s AND function_name = %s
          AND (file_path = %s OR right(file_path, length(%s) + 1) = '/' || %s)
        ORDER BY (file_path = %s) DESC, updated_at DESC
        LIMIT 1
        """,
        (repo, function_name, file_path, file_path, file_path, file_path),
    ).fetchone()

    query_embedding = embed_text(code, embedding_model)

    rows = conn.execute(
        """
        SELECT file_path, function_name, callers, has_tests, incident_tags,
               1 - (embedding <=> %s::vector) AS similarity
        FROM code_chunks
        WHERE repo = %s AND NOT (file_path = %s AND function_name = %s)
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (query_embedding, repo, file_path, function_name, query_embedding, top_k),
    ).fetchall()

    similar = [
        SimilarFunction(
            file_path=r[0],
            function_name=r[1],
            similarity=float(r[5]),
            callers=r[2],
            has_tests=r[3],
            incident_tags=r[4],
        )
        for r in rows
    ]

    return RetrievalResult(
        file_path=file_path,
        function_name=function_name,
        already_indexed=own is not None,
        callers=own[0] if own else [],
        has_tests=own[1] if own else False,
        incident_tags=own[2] if own else [],
        similar=similar,
        code=code,
    )


def retrieve_context_for_pr(
    owner: str,
    name: str,
    pr_number: int,
    head_sha: str,
    database_url: str,
    embedding_model: str,
    token: str | None = None,
    top_k: int = 5,
) -> list[RetrievalResult]:
    """End-to-end bridge (SPEC F3): given a real PR, find every changed
    function and retrieve grounding context for each (this module) — what
    Phase 5's prompt builder will consume directly.

    Mirrors Phase 2's `analyze_pr_diff` skip rules (unsupported language,
    removed files, missing patch) rather than calling it directly, so each
    file's content is fetched exactly once and reused for both changed-line
    detection and chunking — `analyze_pr_diff` alone would fetch it a
    second time here just to get the code text it doesn't itself return.
    """
    repo = f"{owner}/{name}"
    files = fetch_pr_files(owner, name, pr_number, token=token)

    conn = get_connection(database_url)
    try:
        results = []
        for f in files:
            file_path = f["filename"]
            lang = get_language_for_file(file_path)
            if lang is None or f["status"] == "removed":
                continue
            patch = f.get("patch")
            if not patch:
                continue

            source = fetch_file_content(owner, name, file_path, head_sha, token=token)
            fns = changed_functions(source, patch, lang)
            if not fns:
                continue

            chunks_by_key = {(c.function_name, c.start_line): c for c in chunk_file(file_path, source)}
            for fn in fns:
                chunk = chunks_by_key.get((fn["name"], fn["start_line"]))
                code = chunk.code if chunk else ""
                results.append(
                    retrieve_context_for_function(
                        conn, repo, embedding_model, file_path, fn["name"], code, top_k=top_k
                    )
                )
        return results
    finally:
        conn.close()
