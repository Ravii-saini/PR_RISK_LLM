"""Live test against a real historical TypeScript PR
(nestjs/nest#17028, "feat(microservices): expose transport server getter").
Public repo, long-merged/immutable, deterministic despite being live.

Ground truth independently verified by reading the actual patch text for
github.com/nestjs/nest/pull/17028 before writing these assertions.
"""
import pytest

from worker.diff_parser.parser import analyze_pr_diff

PR_OWNER = "nestjs"
PR_REPO = "nest"
PR_NUMBER = 17028
PR_HEAD_SHA = "4063cdb5d75ad68a4780f66fe0bfb7ce7bd4b55a"


@pytest.mark.network
def test_real_ts_pr_diff_reports_correct_changed_functions():
    result = analyze_pr_diff(PR_OWNER, PR_REPO, PR_NUMBER, PR_HEAD_SHA)
    by_file = {r["file"]: r["functions"] for r in result}

    # server.ts only changes an import line and the class's `implements`
    # clause — no method body is touched — must report zero functions,
    # not a false positive on the class.
    assert "packages/microservices/server/server.ts" not in by_file

    # nest-microservice.ts: a brand-new method (getTransportServer) with a
    # multi-line JSDoc comment directly above it — the comment must not be
    # mistaken for part of a different function, and the new method itself
    # must be correctly detected.
    fns = by_file["packages/microservices/nest-microservice.ts"]
    assert [fn["name"] for fn in fns] == ["getTransportServer"]
