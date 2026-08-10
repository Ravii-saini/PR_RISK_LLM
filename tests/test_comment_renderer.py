from worker.llm.comment_renderer import MARKER, render_comment
from worker.llm.schema import Assessment


def test_render_comment_starts_with_marker():
    a = Assessment(risk_level="low", reasons=["r1"], suggested_checks=["c1"])
    body = render_comment(a, "abc123def456")
    assert body.startswith(MARKER)


def test_render_comment_includes_risk_level_and_facts():
    a = Assessment(risk_level="high", reasons=["specific reason about caller X"], suggested_checks=["check Y"])
    body = render_comment(a, "abc123def456")
    assert "High" in body
    assert "specific reason about caller X" in body
    assert "check Y" in body


def test_render_comment_degraded_shows_notice():
    a = Assessment(risk_level="medium", reasons=["unavailable"], suggested_checks=[], degraded=True)
    body = render_comment(a, "abc123def456")
    assert "unavailable" in body.lower()


def test_render_comment_handles_empty_reasons_and_checks():
    a = Assessment(risk_level="low", reasons=[], suggested_checks=[])
    body = render_comment(a, "abc123def456")
    assert "### Why" in body
    assert "### Suggested checks" in body


def test_render_comment_includes_short_commit_sha():
    a = Assessment(risk_level="low", reasons=["x"], suggested_checks=["y"])
    body = render_comment(a, "abc123def456789")
    assert "abc123def456" in body
