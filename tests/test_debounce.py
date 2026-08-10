"""Debounce/supersede tests (SPEC §6): a PR pushed to twice in quick
succession should only actually get processed once, for the latest SHA.
Uses a short debounce window (0.3s, set in conftest) instead of production's
20s so this runs fast without changing the logic under test.
"""
import json

from tests.helpers import make_payload, webhook_headers
from worker.debounce import process_next_job


def test_single_event_processed_after_debounce_window(client, settings, redis_client):
    payload = make_payload(repo="octo/demo", pr_number=200, head_sha="sha-single")
    body = json.dumps(payload).encode()
    client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )

    result = process_next_job(
        redis_client,
        debounce_seconds=settings.debounce_window_seconds,
        stream=settings.stream_name,
    )

    assert result is not None
    assert result["outcome"] == "processed"
    assert result["job"]["head_sha"] == "sha-single"


def test_rapid_double_push_only_latest_sha_processed(client, settings, redis_client):
    repo, pr = "octo/demo", 201

    p1 = make_payload(repo=repo, pr_number=pr, head_sha="sha-old", action="synchronize")
    b1 = json.dumps(p1).encode()
    client.post(
        "/webhooks/github", content=b1, headers=webhook_headers(settings.github_webhook_secret, b1)
    )

    # Second push lands for the same PR before the first job's debounce wait completes.
    p2 = make_payload(repo=repo, pr_number=pr, head_sha="sha-new", action="synchronize")
    b2 = json.dumps(p2).encode()
    client.post(
        "/webhooks/github", content=b2, headers=webhook_headers(settings.github_webhook_secret, b2)
    )

    first = process_next_job(
        redis_client, debounce_seconds=settings.debounce_window_seconds, stream=settings.stream_name
    )
    second = process_next_job(
        redis_client, debounce_seconds=settings.debounce_window_seconds, stream=settings.stream_name
    )

    outcomes = {first["job"]["head_sha"]: first["outcome"], second["job"]["head_sha"]: second["outcome"]}
    assert outcomes["sha-old"] == "superseded"
    assert outcomes["sha-new"] == "processed"


def test_on_processed_callback_fires_only_for_processed_outcome(client, settings, redis_client):
    payload = make_payload(repo="octo/demo", pr_number=210, head_sha="sha-callback")
    body = json.dumps(payload).encode()
    client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )

    calls = []
    result = process_next_job(
        redis_client,
        debounce_seconds=settings.debounce_window_seconds,
        stream=settings.stream_name,
        on_processed=lambda job: calls.append(job),
    )

    assert result["outcome"] == "processed"
    assert len(calls) == 1
    assert calls[0]["head_sha"] == "sha-callback"


def test_on_processed_callback_does_not_fire_for_superseded_outcome(client, settings, redis_client):
    repo, pr = "octo/demo", 211

    p1 = make_payload(repo=repo, pr_number=pr, head_sha="sha-super-old", action="synchronize")
    b1 = json.dumps(p1).encode()
    client.post(
        "/webhooks/github", content=b1, headers=webhook_headers(settings.github_webhook_secret, b1)
    )
    p2 = make_payload(repo=repo, pr_number=pr, head_sha="sha-super-new", action="synchronize")
    b2 = json.dumps(p2).encode()
    client.post(
        "/webhooks/github", content=b2, headers=webhook_headers(settings.github_webhook_secret, b2)
    )

    calls = []
    process_next_job(
        redis_client,
        debounce_seconds=settings.debounce_window_seconds,
        stream=settings.stream_name,
        on_processed=lambda job: calls.append(job["head_sha"]),
    )
    process_next_job(
        redis_client,
        debounce_seconds=settings.debounce_window_seconds,
        stream=settings.stream_name,
        on_processed=lambda job: calls.append(job["head_sha"]),
    )

    assert calls == ["sha-super-new"]  # never called for the superseded old SHA


def test_on_processed_exception_does_not_propagate(client, settings, redis_client):
    """SPEC §5.2: ack happens after the debounce decision, not after full
    downstream success — a crashing review pipeline must not wedge the
    consumer group or blow up the caller.
    """
    payload = make_payload(repo="octo/demo", pr_number=212, head_sha="sha-crash")
    body = json.dumps(payload).encode()
    client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(settings.github_webhook_secret, body),
    )

    def boom(job):
        raise RuntimeError("simulated review pipeline crash")

    result = process_next_job(
        redis_client,
        debounce_seconds=settings.debounce_window_seconds,
        stream=settings.stream_name,
        on_processed=boom,
    )  # must not raise

    assert result["outcome"] == "processed"
