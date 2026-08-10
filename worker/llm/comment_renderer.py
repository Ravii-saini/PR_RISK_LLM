"""Renders a parsed Assessment into a clean PR comment body (SPEC §5.5:
"parsed and rendered into a clean PR comment — not raw LLM prose pasted
in"). The leading HTML comment is a hidden marker (SPEC §5.6) used to find
this bot's own prior comment on a PR so `synchronize` events update it in
place instead of stacking a new one each time.
"""
from worker.llm.schema import Assessment

MARKER = "<!-- pr-risk-copilot:assessment -->"

_RISK_LABEL = {"low": "Low", "medium": "Medium", "high": "High"}


def render_comment(assessment: Assessment, commit_sha: str) -> str:
    label = _RISK_LABEL.get(assessment.risk_level, assessment.risk_level.title())
    lines = [
        MARKER,
        "## PR Risk Assessment",
        "",
        f"**Risk level:** {label}",
        "",
    ]

    if assessment.degraded:
        lines.append("_Automated analysis was unavailable for this revision — see reasons below._")
        lines.append("")

    lines.append("### Why")
    if assessment.reasons:
        lines.extend(f"- {r}" for r in assessment.reasons)
    else:
        lines.append("- No specific reasons were returned.")
    lines.append("")

    lines.append("### Suggested checks")
    if assessment.suggested_checks:
        lines.extend(f"- {c}" for c in assessment.suggested_checks)
    else:
        lines.append("- None.")
    lines.append("")

    lines.append(f"<sub>Automated review of `{commit_sha[:12]}` — not a substitute for human review.</sub>")

    return "\n".join(lines)
