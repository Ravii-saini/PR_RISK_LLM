-- pgvector schema for the retrieval layer (SPEC §5.4).
-- Applied idempotently by worker/retrieval/db.py:ensure_schema() on startup.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per repo tracking the last commit SHA that has been fully
-- indexed, so the scheduled reindex job (SPEC §5.1b) can diff against it
-- instead of re-embedding the whole repo every run.
CREATE TABLE IF NOT EXISTS indexed_repos (
    repo TEXT PRIMARY KEY,
    last_indexed_sha TEXT NOT NULL,
    last_indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per indexed function/method chunk (SPEC §5.4: chunked by
-- function, not fixed-size text blocks).
--
-- `callers` is a simple name-based static call-graph result (SPEC §5.4:
-- "known callers... built via a simple static call-graph pass") — it is
-- NOT import-resolved; it records other indexed functions anywhere in the
-- repo whose body contains a call to this function's name. Two functions
-- that happen to share a name will each be recorded as a caller of the
-- other's callees. Documented limitation, not a bug: a fully import-
-- resolved call graph is out of scope for "simple".
CREATE TABLE IF NOT EXISTS code_chunks (
    id BIGSERIAL PRIMARY KEY,
    repo TEXT NOT NULL,
    file_path TEXT NOT NULL,
    function_name TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    code TEXT NOT NULL,
    embedding VECTOR(768) NOT NULL,
    callers JSONB NOT NULL DEFAULT '[]',
    has_tests BOOLEAN NOT NULL DEFAULT FALSE,
    incident_tags JSONB NOT NULL DEFAULT '[]',
    commit_sha TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo, file_path, function_name, start_line)
);

CREATE INDEX IF NOT EXISTS code_chunks_repo_file_idx
    ON code_chunks (repo, file_path);

-- IVFFlat needs at least a handful of rows to train on; harmless to create
-- early since it degrades to a sequential scan below that, and the eval
-- repo's chunk count will clear it easily.
CREATE INDEX IF NOT EXISTS code_chunks_embedding_idx
    ON code_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
