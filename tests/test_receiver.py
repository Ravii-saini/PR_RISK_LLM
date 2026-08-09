"""Receiver tests (SPEC §5.1, §6, §3.5): signature verification, allowlist,
dedup, fast-ack, and the concurrency/duplicate-delivery cases that are the
real point of Phase 1 — not the happy path alone.
"""
import json
import threading

from tests.helpers import make_payload, webhook_headers


def test_missing_signature_rejected(client):
    body = json.dumps(make_payload()).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_invalid_signature_rejected(client):
    body = json.dumps(make_payload()).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_malformed_json_body_rejected(client, settings):
    """Valid signature over an invalid JSON body must not crash the handler (500)."""
    body = b"not valid json at all"
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )
    assert resp.status_code == 400


def test_payload_missing_pull_request_fields_rejected(client, settings):
    """Valid signature + valid JSON, but structurally incomplete, must 400 not 500."""
    body = json.dumps({"action": "opened", "repository": {"full_name": "octo/demo"}}).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )
    assert resp.status_code == 400


def test_ping_event_ignored(client, settings):
    body = json.dumps({"zen": "hello"}).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body, event="ping"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_unhandled_action_ignored(client, settings):
    payload = make_payload(action="closed")
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_repo_not_in_allowlist_rejected(client, settings):
    payload = make_payload(repo="octo/not-allowed")
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_valid_event_enqueues_job(client, settings, redis_client):
    payload = make_payload(repo="octo/demo", pr_number=10, head_sha="sha-valid")
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "enqueued"

    entries = redis_client.xrange(settings.stream_name)
    assert len(entries) == 1
    job = json.loads(entries[0][1]["data"])
    assert job["repo"] == "octo/demo"
    assert job["pr_number"] == 10
    assert job["head_sha"] == "sha-valid"


def test_duplicate_delivery_only_one_job_enqueued(client, settings, redis_client):
    """F6/N3: re-delivering the same webhook must not produce a second job."""
    payload = make_payload(repo="octo/demo", pr_number=55, head_sha="deadbeef")
    body = json.dumps(payload).encode()
    headers = webhook_headers(settings.github_webhook_secret, body)

    first = client.post("/webhooks/github", content=body, headers=headers)
    second = client.post("/webhooks/github", content=body, headers=headers)

    assert first.json()["status"] == "enqueued"
    assert second.json()["status"] == "duplicate"

    entries = redis_client.xrange(settings.stream_name)
    assert len(entries) == 1


def test_concurrent_delivery_two_prs_no_cross_contamination(client, settings, redis_client):
    """F7: two PRs landing at once must not corrupt or merge each other's state."""
    results = {}

    def fire(pr_number, head_sha):
        payload = make_payload(repo="octo/demo", pr_number=pr_number, head_sha=head_sha)
        body = json.dumps(payload).encode()
        headers = webhook_headers(settings.github_webhook_secret, body)
        resp = client.post("/webhooks/github", content=body, headers=headers)
        results[pr_number] = resp.json()

    t1 = threading.Thread(target=fire, args=(101, "sha-pr101"))
    t2 = threading.Thread(target=fire, args=(102, "sha-pr102"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results[101]["status"] == "enqueued"
    assert results[102]["status"] == "enqueued"

    entries = redis_client.xrange(settings.stream_name)
    assert len(entries) == 2
    jobs = {json.loads(fields["data"])["pr_number"]: json.loads(fields["data"]) for _, fields in entries}
    assert jobs[101]["repo"] == "octo/demo"
    assert jobs[101]["head_sha"] == "sha-pr101"
    assert jobs[102]["repo"] == "octo/demo"
    assert jobs[102]["head_sha"] == "sha-pr102"
