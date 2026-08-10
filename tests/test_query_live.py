"""Live spot-check (SPEC §5.4/PLAN.md Phase 4: "spot-check retrieval output
against a few real diffs before moving on"), encoded as real assertions
rather than one-off manual inspection. Network-marked for the same reason
as Phase 2/3's live tests — run explicitly with `uv run pytest -m network`.

Requires the pinned eval repo (psf/requests) to already be indexed (Phase
3's live indexer test / manual run populates this).
"""
import pytest

from worker.retrieval.query import retrieve_context_for_pr

pytestmark = pytest.mark.network

EVAL_OWNER, EVAL_NAME = "psf", "requests"


def test_retrieval_surfaces_real_incident_tag_and_callers(settings):
    # PR #6965: the actual merged fix for CVE-2024-47081, touching exactly
    # one function (get_netrc_auth). Ground truth verified by hand in
    # Phase 3 against the real PR diff (see incident_tags.json).
    results = retrieve_context_for_pr(
        EVAL_OWNER,
        EVAL_NAME,
        6965,
        "57acb7c26d809cf864ec439b8bcd6364702022d5",
        settings.database_url,
        settings.embedding_model,
        top_k=3,
    )

    assert len(results) == 1
    r = results[0]
    assert r.file_path == "src/requests/utils.py"
    assert r.function_name == "get_netrc_auth"
    assert r.already_indexed is True
    assert [t["tag"] for t in r.incident_tags] == ["CVE-2024-47081"]
    assert "src/requests/sessions.py::rebuild_auth" in r.callers
    assert r.has_tests is True
    assert len(r.similar) == 3


def test_retrieval_does_not_hallucinate_incident_tags_on_unrelated_pr(settings):
    # PR #7431: a real merged PR (also used as Phase 2's ground-truth live
    # test) touching models.py.__init__ and sessions.py.request — neither
    # function is in the curated incident set. Negative-case check: no
    # incident tags should be fabricated for functions that don't have any.
    results = retrieve_context_for_pr(
        EVAL_OWNER,
        EVAL_NAME,
        7431,
        "0a074cadfaccfbdd2f841d567a4c1a9132a72cd4",
        settings.database_url,
        settings.embedding_model,
        top_k=3,
    )

    assert len(results) == 2
    by_name = {r.function_name: r for r in results}
    assert by_name["__init__"].incident_tags == []
    assert by_name["request"].incident_tags == []
    # "request" is genuinely called by every HTTP-verb convenience method —
    # real, verifiable structural signal, not a placeholder.
    assert "src/requests/sessions.py::get" in by_name["request"].callers
    assert "src/requests/sessions.py::post" in by_name["request"].callers
