"""Shared helpers for building signed GitHub webhook test payloads."""
import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_payload(repo="octo/demo", pr_number=42, head_sha="abc123", action="opened"):
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "number": pr_number,
            "head": {"sha": head_sha},
        },
        "repository": {"full_name": repo},
    }


def webhook_headers(secret: str, body: bytes, event: str = "pull_request") -> dict:
    return {
        "X-Hub-Signature-256": sign(secret, body),
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }
