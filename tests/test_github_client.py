"""GitHub client tests (SPEC §5.3): pagination is mocked (respx, since the
client uses httpx — `responses` only mocks the `requests` library), and
correctness against real GitHub data is proven separately in
tests/test_diff_parser_live.py against a real historical PR.
"""
import httpx
import respx

from worker.github_client import fetch_pr_files


@respx.mock
def test_fetch_pr_files_follows_pagination():
    """A PR with more files than fit on one page must not silently drop the
    rest — GitHub caps this endpoint at 100/page and signals more via the
    Link header's rel="next" (SPEC §5.3).
    """
    page1_url = "https://api.github.com/repos/octo/demo/pulls/1/files"
    page2_url = "https://api.github.com/repos/octo/demo/pulls/1/files?page=2"

    respx.get(page1_url, params={"per_page": "100"}).mock(
        return_value=httpx.Response(
            200,
            json=[{"filename": f"file{i}.py"} for i in range(100)],
            headers={"Link": f'<{page2_url}>; rel="next"'},
        )
    )
    respx.get(page2_url).mock(
        return_value=httpx.Response(200, json=[{"filename": "file100.py"}])
    )

    files = fetch_pr_files("octo", "demo", 1)

    assert len(files) == 101
    assert files[0]["filename"] == "file0.py"
    assert files[-1]["filename"] == "file100.py"


@respx.mock
def test_fetch_pr_files_single_page_no_next_link():
    respx.get("https://api.github.com/repos/octo/demo/pulls/2/files").mock(
        return_value=httpx.Response(200, json=[{"filename": "only.py"}])
    )

    files = fetch_pr_files("octo", "demo", 2)

    assert len(files) == 1
    assert files[0]["filename"] == "only.py"
