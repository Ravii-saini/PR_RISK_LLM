"""Postgres+pgvector connection and schema management (SPEC §5.4, §9)."""
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, autocommit=True)
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    """Idempotently create the vector extension and tables. Safe to call on
    every startup — every statement is CREATE ... IF NOT EXISTS.
    """
    conn.execute(_SCHEMA_PATH.read_text())


def get_last_indexed_sha(conn: psycopg.Connection, repo: str) -> str | None:
    """Return the SHA this repo was last indexed at, or None if it has
    never been indexed — the reindex job uses this to decide between a
    full index and an incremental diff-against-last-SHA pass.
    """
    row = conn.execute(
        "SELECT last_indexed_sha FROM indexed_repos WHERE repo = %s", (repo,)
    ).fetchone()
    return row[0] if row else None


def set_last_indexed_sha(conn: psycopg.Connection, repo: str, sha: str) -> None:
    conn.execute(
        """
        INSERT INTO indexed_repos (repo, last_indexed_sha, last_indexed_at)
        VALUES (%s, %s, now())
        ON CONFLICT (repo) DO UPDATE
            SET last_indexed_sha = EXCLUDED.last_indexed_sha,
                last_indexed_at = now()
        """,
        (repo, sha),
    )
