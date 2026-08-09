"""Live test against a real historical Go PR (gin-gonic/gin#4145, "add bson
protocol"). Public repo, long-merged/immutable, deterministic despite being
a live network call.

Ground truth independently verified by reading the actual patch text for
github.com/gin-gonic/gin/pull/4145 before writing these assertions.
"""
import pytest

from worker.diff_parser.parser import analyze_pr_diff

PR_OWNER = "gin-gonic"
PR_REPO = "gin"
PR_NUMBER = 4145
PR_HEAD_SHA = "db50ea7b54394e7ba9ceb83d7c799accdd9387b1"


@pytest.mark.network
def test_real_go_pr_diff_reports_correct_changed_functions():
    result = analyze_pr_diff(PR_OWNER, PR_REPO, PR_NUMBER, PR_HEAD_SHA)
    by_file = {r["file"]: r["functions"] for r in result}

    # binding.go and binding_nomsgpack.go each only get one real code change
    # inside a function body (a new `case MIMEBSON:` branch) — the rest of
    # each diff touches const/var blocks at file scope, not inside any func.
    assert [fn["name"] for fn in by_file["binding/binding.go"]] == ["Default"]
    assert [fn["name"] for fn in by_file["binding/binding_nomsgpack.go"]] == ["Default"]

    # context.go: a brand-new method (BSON) plus an existing one (Negotiate)
    # that got a new case added inside its body.
    context_fns = {fn["name"] for fn in by_file["context.go"]}
    assert context_fns == {"BSON", "Negotiate"}
