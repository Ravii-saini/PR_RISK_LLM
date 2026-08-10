"""GitHub REST API client: App installation auth, paginated PR file listing,
and file content fetching at a ref (SPEC §5.3, §5.6).
"""
import base64
import time

import httpx
import jwt

GITHUB_API = "https://api.github.com"
_TIMEOUT = httpx.Timeout(15.0)


def make_app_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 300, "iss": app_id}
    return jwt.encode(payload, private_key, algorithm="RS256")


def _app_headers(app_id: str, private_key: str) -> dict:
    return {
        "Authorization": f"Bearer {make_app_jwt(app_id, private_key)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_installation_id(app_id: str, private_key: str, owner: str, repo: str) -> int:
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/installation",
        headers=_app_headers(app_id, private_key),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_installation_token(app_id: str, private_key: str, installation_id: int) -> str:
    resp = httpx.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers=_app_headers(app_id, private_key),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _auth_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_pr_files(owner: str, repo: str, pr_number: int, token: str | None = None) -> list[dict]:
    """Fetch every file changed in a PR, following pagination (SPEC §5.3) —
    GitHub caps this endpoint at 100 files/page, and large PRs need every page,
    not just the first.
    """
    headers = _auth_headers(token)
    files: list[dict] = []
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/files"
    params = {"per_page": 100}
    with httpx.Client(timeout=_TIMEOUT) as client:
        while url:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            files.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
            params = None  # the "next" URL already carries its own query params
    return files


def get_default_branch_head_sha(owner: str, repo: str, token: str | None = None) -> str:
    """SPEC §5.1b: the reindex job pulls "the latest main" — resolve that to
    a concrete commit SHA via the repo's actual default branch (not
    hardcoded "main", since not every repo uses that name).
    """
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}",
        headers=_auth_headers(token),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    default_branch = resp.json()["default_branch"]
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/commits/{default_branch}",
        headers=_auth_headers(token),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["sha"]


def list_repo_tree(owner: str, repo: str, sha: str, token: str | None = None) -> list[str]:
    """Return every file path in the repo at `sha` (recursive). Raises if
    GitHub truncates the response (repo too large for one non-paginated
    call) rather than silently indexing a partial tree.
    """
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{sha}",
        headers=_auth_headers(token),
        params={"recursive": "1"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("truncated"):
        raise ValueError(
            f"Repo tree for {owner}/{repo}@{sha} was truncated by GitHub's API — "
            "needs a paginated/incremental tree-walking strategy, not supported yet."
        )
    return [item["path"] for item in data["tree"] if item["type"] == "blob"]


def get_changed_files_between(
    owner: str, repo: str, base_sha: str, head_sha: str, token: str | None = None
) -> list[dict]:
    """SPEC §5.1b: "diffs it against the last-indexed commit SHA" — used by
    the reindex job to re-embed only changed functions instead of the whole
    repo on every run.
    """
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
        headers=_auth_headers(token),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def fetch_file_content(owner: str, repo: str, path: str, ref: str, token: str | None = None) -> str:
    """Fetch a file's full text content at a specific ref (e.g. a PR's head SHA)."""
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        headers=_auth_headers(token),
        params={"ref": ref},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("encoding") != "base64":
        raise ValueError(f"Unexpected encoding for {path}: {data.get('encoding')}")
    return base64.b64decode(data["content"]).decode("utf-8")
