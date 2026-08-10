"""Comment posting + update-on-synchronize tests (SPEC §5.6)."""
import httpx
import respx

from worker.github_client import find_comment_with_marker, post_or_update_comment

MARKER = "<!-- pr-risk-copilot:assessment -->"


@respx.mock
def test_find_comment_with_marker_returns_matching_comment():
    respx.get("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "body": "a human comment, no marker"},
                {"id": 2, "body": f"{MARKER}\nprevious assessment"},
            ],
        )
    )

    found = find_comment_with_marker("octo", "demo", 5, MARKER)
    assert found["id"] == 2


@respx.mock
def test_find_comment_with_marker_returns_none_when_absent():
    respx.get("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "body": "just a human comment"}])
    )

    assert find_comment_with_marker("octo", "demo", 5, MARKER) is None


@respx.mock
def test_post_or_update_comment_creates_when_none_exists():
    respx.get("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    create_route = respx.post("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(201, json={"id": 99, "body": f"{MARKER}\nnew"})
    )

    result = post_or_update_comment("octo", "demo", 5, f"{MARKER}\nnew", MARKER)

    assert create_route.called
    assert result["id"] == 99


@respx.mock
def test_post_or_update_comment_updates_existing_marker_comment():
    respx.get("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(
            200, json=[{"id": 2, "body": f"{MARKER}\nold assessment"}]
        )
    )
    update_route = respx.patch("https://api.github.com/repos/octo/demo/issues/comments/2").mock(
        return_value=httpx.Response(200, json={"id": 2, "body": f"{MARKER}\nnew assessment"})
    )
    create_route = respx.post("https://api.github.com/repos/octo/demo/issues/5/comments")

    result = post_or_update_comment("octo", "demo", 5, f"{MARKER}\nnew assessment", MARKER)

    assert update_route.called
    assert not create_route.called
    assert result["body"] == f"{MARKER}\nnew assessment"


@respx.mock
def test_post_or_update_comment_does_not_touch_human_comments():
    respx.get("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "body": "great work, LGTM"},
                {"id": 2, "body": "one nit: typo on line 4"},
            ],
        )
    )
    create_route = respx.post("https://api.github.com/repos/octo/demo/issues/5/comments").mock(
        return_value=httpx.Response(201, json={"id": 3, "body": f"{MARKER}\nassessment"})
    )
    update_route = respx.patch(url__regex=r".*/issues/comments/\d+")

    result = post_or_update_comment("octo", "demo", 5, f"{MARKER}\nassessment", MARKER)

    assert create_route.called
    assert not update_route.called
    assert result["id"] == 3
