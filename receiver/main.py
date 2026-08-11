"""Webhook receiver: validates GitHub deliveries, dedupes, and enqueues — nothing else.

Fast-ack design (SPEC §4): this handler does no real work. It verifies the
signature, checks the repo allowlist, claims the dedup key, and enqueues to
Redis Streams, then returns 200 immediately. All actual PR processing
(diff fetch, retrieval, LLM, comment posting) happens in the worker.
"""
import hashlib
import hmac
import json
import time

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from config import Settings, get_settings
from receiver.redis_client import get_redis
from worker.redis_keys import latest_sha_key

app = FastAPI()

PROCESSABLE_ACTIONS = {"opened", "synchronize"}


def verify_signature(payload_body: bytes, signature_header: str | None, secret: str) -> None:
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")
    expected = "sha256=" + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


def dedup_key(repo: str, pr_number: int, head_sha: str) -> str:
    return f"dedup:{repo}:{pr_number}:{head_sha}"


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    r: redis.Redis = Depends(get_redis),
):
    body = await request.body()
    verify_signature(body, x_hub_signature_256, settings.github_webhook_secret)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event '{x_github_event}' not handled"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    action = payload.get("action")
    if action not in PROCESSABLE_ACTIONS:
        return {"status": "ignored", "reason": f"action '{action}' not processed"}

    try:
        repo = payload["repository"]["full_name"]
        pr_number = payload["pull_request"]["number"]
        head_sha = payload["pull_request"]["head"]["sha"]
    except (KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Payload missing expected pull_request fields")

    # Repo allowlist check (SPEC §5.1) — the App is installed on more than one
    # repo; only the demo-target repo should ever be enqueued for processing.
    if repo not in settings.allowed_repos:
        return {"status": "rejected", "reason": "repo not in allowlist"}

    # Atomic dedup claim (F6, N3): SET NX means only the first delivery of this
    # exact (repo, PR, head SHA) combination wins the race, whether the
    # duplicate is a GitHub redelivery or two near-simultaneous deliveries.
    key = dedup_key(repo, pr_number, head_sha)
    claimed = r.set(key, "1", nx=True, ex=settings.dedup_key_ttl_seconds)
    if not claimed:
        return {"status": "duplicate", "reason": "already processed or queued"}

    r.set(latest_sha_key(repo, pr_number), head_sha)

    job = {
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "action": action,
        "enqueued_at": time.time(),
    }
    r.xadd(settings.stream_name, {"data": json.dumps(job)})

    return {"status": "enqueued", "repo": repo, "pr_number": pr_number, "head_sha": head_sha}
