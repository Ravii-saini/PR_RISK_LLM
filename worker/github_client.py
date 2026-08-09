"""GitHub REST API client: App installation auth, paginated PR file listing,
and file content fetching at a ref (SPEC §5.3, §5.6).
"""
import base64
import time

import httpx
import jwt

GITHUB_API = "https://api.github.com"


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
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_installation_token(app_id: str, private_key: str, installation_id: int) -> str:
    resp = httpx.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers=_app_headers(app_id, private_key),
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
    with httpx.Client() as client:
        while url:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            files.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
            params = None  # the "next" URL already carries its own query params
    return files


def fetch_file_content(owner: str, repo: str, path: str, ref: str, token: str | None = None) -> str:
    """Fetch a file's full text content at a specific ref (e.g. a PR's head SHA)."""
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        headers=_auth_headers(token),
        params={"ref": ref},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("encoding") != "base64":
        raise ValueError(f"Unexpected encoding for {path}: {data.get('encoding')}")
    return base64.b64decode(data["content"]).decode("utf-8")
