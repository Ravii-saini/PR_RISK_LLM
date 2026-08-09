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

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from config import get_settings
from receiver.main import app
from receiver.redis_client import get_redis


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
