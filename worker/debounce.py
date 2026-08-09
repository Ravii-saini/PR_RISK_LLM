"""Worker-side debounce/supersede handling (SPEC §6).

The receiver must fast-ack, so it can't itself wait out the debounce window.
Instead: the worker dequeues a job immediately, waits, then re-checks whether
a newer head SHA has landed for the same PR in the meantime. If so, this job
was superseded by a later push and is skipped rather than processed.

This module only proves the debounce/supersede decision for Phase 1 — later
phases replace the "processed" branch with the real diff/retrieval/LLM/comment
pipeline.
"""
import json
import time

import redis

from config import get_settings

STREAM_NAME = "pr_events"
GROUP_NAME = "pr_workers"
CONSUMER_NAME = "worker-1"


def latest_sha_key(repo: str, pr_number: int) -> str:
    return f"latest_sha:{repo}:{pr_number}"


def ensure_group(r: redis.Redis, stream: str = STREAM_NAME, group: str = GROUP_NAME) -> None:
    try:
        r.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def process_next_job(
    r: redis.Redis,
    debounce_seconds: float,
    stream: str = STREAM_NAME,
    group: str = GROUP_NAME,
    consumer: str = CONSUMER_NAME,
    block_ms: int = 1000,
) -> dict | None:
    """Dequeue one job and apply the debounce/supersede check.

    Returns None if no job was available, otherwise
    {"job": ..., "outcome": "processed" | "superseded", "message_id": ...}.
    """
    ensure_group(r, stream=stream, group=group)

    resp = r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=block_ms)
    if not resp:
        return None

    _, messages = resp[0]
    msg_id, fields = messages[0]
    job = json.loads(fields["data"])

    time.sleep(debounce_seconds)

    current_latest = r.get(latest_sha_key(job["repo"], job["pr_number"]))
    outcome = "processed" if current_latest == job["head_sha"] else "superseded"

    r.xack(stream, group, msg_id)

    if outcome == "processed":
        r.rpush("processed_jobs", json.dumps(job))

    return {"job": job, "outcome": outcome, "message_id": msg_id}


def run_forever() -> None:
    settings = get_settings()
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    ensure_group(r, stream=settings.stream_name)
    while True:
        process_next_job(r, settings.debounce_window_seconds, stream=settings.stream_name)


if __name__ == "__main__":
    run_forever()
