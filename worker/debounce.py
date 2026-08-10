"""Worker-side debounce/supersede handling (SPEC §6).

The receiver must fast-ack, so it can't itself wait out the debounce window.
Instead: the worker dequeues a job immediately, waits, then re-checks whether
a newer head SHA has landed for the same PR in the meantime. If so, this job
was superseded by a later push and is skipped rather than processed.

`process_next_job`'s `on_processed` callback is the seam where the real
diff/retrieval/LLM/comment pipeline (worker.review.review_pr) plugs in —
kept as an injectable parameter (defaulting to a no-op) rather than a
hardcoded call, so Phase 1's debounce/supersede tests keep exercising just
the debounce logic itself, not the full pipeline.
"""
import json
import logging
import time

import redis

from config import get_settings

logger = logging.getLogger(__name__)

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
    on_processed=None,
) -> dict | None:
    """Dequeue one job and apply the debounce/supersede check.

    Returns None if no job was available, otherwise
    {"job": ..., "outcome": "processed" | "superseded", "message_id": ...}.

    `on_processed(job)`, if given, runs only for the "processed" outcome
    (never for "superseded"). A failure in it is logged, not raised — the
    job is still acked either way (SPEC §5.2: ack after the debounce
    decision, not after full downstream success, so a crash-prone review
    pipeline can't wedge the consumer group).
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
        if on_processed is not None:
            try:
                on_processed(job)
            except Exception:
                logger.exception(
                    "Review pipeline failed for %s#%s (%s) — job already acked, not auto-retried",
                    job["repo"],
                    job["pr_number"],
                    job["head_sha"],
                )

    return {"job": job, "outcome": outcome, "message_id": msg_id}


def _review_job(job: dict, settings) -> None:
    from worker.review import review_pr

    owner, name = job["repo"].split("/", 1)
    result = review_pr(owner, name, job["pr_number"], job["head_sha"], settings)
    logger.info("Reviewed %s#%s: %s", job["repo"], job["pr_number"], result)


def run_forever() -> None:
    settings = get_settings()
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    ensure_group(r, stream=settings.stream_name)
    while True:
        process_next_job(
            r,
            settings.debounce_window_seconds,
            stream=settings.stream_name,
            on_processed=lambda job: _review_job(job, settings),
        )


if __name__ == "__main__":
    run_forever()
