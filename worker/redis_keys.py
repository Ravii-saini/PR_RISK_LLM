"""Redis key builder shared by the receiver and the worker — was defined
identically in both places, which risked the two silently drifting apart.
"""


def latest_sha_key(repo: str, pr_number: int) -> str:
    return f"latest_sha:{repo}:{pr_number}"
