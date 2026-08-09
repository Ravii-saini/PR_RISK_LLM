"""Live test against a real, historical, multi-file PR (PLAN Phase 2's own
exit criterion: "test against a real multi-file... PR diff"). Hits the real
public GitHub API — no auth needed, psf/requests is public and this PR is
long-merged/immutable, so this is deterministic despite being a live call.

Ground truth below was independently verified by reading the actual patch
text for github.com/psf/requests/pull/7431 before writing these assertions,
not just accepted from the tool's own output.
"""
import pytest

from worker.diff_parser.parser import analyze_pr_diff

PR_OWNER = "psf"
PR_REPO = "requests"
PR_NUMBER = 7431
PR_HEAD_SHA = "0a074cadfaccfbdd2f841d567a4c1a9132a72cd4"


@pytest.mark.network
def test_real_multi_file_pr_diff_reports_correct_changed_functions():
    result = analyze_pr_diff(PR_OWNER, PR_REPO, PR_NUMBER, PR_HEAD_SHA)
    by_file = {r["file"]: r["functions"] for r in result}

    # src/requests/_types.py only touches TypeAlias/TypedDict-field lines at
    # class level — no `def` anywhere near the diff — so it must report zero
    # functions, not a false positive on the enclosing class.
    assert "src/requests/_types.py" not in by_file

    # The real change in models.py lands inside __init__'s multi-line
    # signature (the `headers` parameter's type annotation), not on the
    # `def __init__` line itself — this is the exact case a single-line
    # regex-based detector cannot handle correctly.
    assert [fn["name"] for fn in by_file["src/requests/models.py"]] == ["__init__"]

    # Same shape of case in sessions.py: the change is inside `request`'s
    # multi-line signature.
    assert [fn["name"] for fn in by_file["src/requests/sessions.py"]] == ["request"]
