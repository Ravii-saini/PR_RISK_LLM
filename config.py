"""Shared configuration for the receiver and worker processes."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    github_webhook_secret: str
    allowed_repos: frozenset[str]
    redis_url: str
    dedup_key_ttl_seconds: int
    debounce_window_seconds: float
    stream_name: str = "pr_events"


def get_settings() -> Settings:
    return Settings(
        github_webhook_secret=os.environ["GITHUB_WEBHOOK_SECRET"],
        allowed_repos=frozenset(
            r.strip() for r in os.environ.get("ALLOWED_REPOS", "").split(",") if r.strip()
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        dedup_key_ttl_seconds=int(os.environ.get("DEDUP_KEY_TTL_SECONDS", "3600")),
        debounce_window_seconds=float(os.environ.get("DEBOUNCE_WINDOW_SECONDS", "20")),
    )
