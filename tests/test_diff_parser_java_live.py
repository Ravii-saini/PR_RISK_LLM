"""Live test against a real historical Java PR
(google/gson#3076, "Make LazilyParsedNumber implement Comparable").
Public repo, long-merged/immutable, deterministic despite being live.

Ground truth independently verified by reading the actual patch text for
github.com/google/gson/pull/3076 before writing these assertions.
"""
import pytest

from worker.diff_parser.parser import analyze_pr_diff

PR_OWNER = "google"
PR_REPO = "gson"
PR_NUMBER = 3076
PR_HEAD_SHA = "66a83419bf6ddbdf2414a93380de314614e311cb"


@pytest.mark.network
def test_real_java_pr_diff_reports_correct_changed_methods():
    result = analyze_pr_diff(PR_OWNER, PR_REPO, PR_NUMBER, PR_HEAD_SHA)
    by_file = {r["file"]: r["functions"] for r in result}

    main_file = "gson/src/main/java/com/google/gson/internal/LazilyParsedNumber.java"
    fn_names = {fn["name"] for fn in by_file[main_file]}

    # The class declaration itself changed (`implements Comparable<...>`)
    # but that's not inside any method — must not be misattributed to one.
    # `writeReplace`'s own `throws` clause changed (its signature line), and
    # `compareTo` is a brand-new method with an @Override annotation and a
    # multi-line Javadoc comment directly above it — both must be flagged.
    assert fn_names == {"writeReplace", "compareTo"}
