"""Test config: force known values before receiver/config.py loads .env,
and point Redis at a dedicated test DB (15) on the same running container
so tests never touch dev data in DB 0.
"""
import os

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["ALLOWED_REPOS"] = "octo/demo,octo/other"
os.environ["REDIS_URL"] = "redis://localhost:6380/15"
os.environ["DEDUP_KEY_TTL_SECONDS"] = "3600"
os.environ["DEBOUNCE_WINDOW_SECONDS"] = "0.3"
os.environ.setdefault(
    "DATABASE_URL", "postgresql://prriskcopilot:prriskcopilot@localhost:5432/prriskcopilot"
)

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from config import get_settings
from receiver.main import app
from receiver.redis_client import get_redis
from worker.retrieval.db import ensure_schema, get_connection

# Tests never touch a separate physical database — code_chunks/indexed_repos
# are already repo-scoped, so isolation comes from using a repo name real
# indexing will never write to, cleaned up before and after each test.
TEST_REPO = "test-org/does-not-exist"


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def redis_client(settings):
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    r.flushdb()
    yield r
    r.flushdb()
    r.close()


@pytest.fixture
def client(redis_client):
    app.dependency_overrides[get_redis] = lambda: redis_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _clean_test_repo_rows(conn):
    conn.execute("DELETE FROM code_chunks WHERE repo = %s", (TEST_REPO,))
    conn.execute("DELETE FROM indexed_repos WHERE repo = %s", (TEST_REPO,))


@pytest.fixture
def pg_conn(settings):
    conn = get_connection(settings.database_url)
    ensure_schema(conn)
    _clean_test_repo_rows(conn)
    yield conn
    _clean_test_repo_rows(conn)
    conn.close()
