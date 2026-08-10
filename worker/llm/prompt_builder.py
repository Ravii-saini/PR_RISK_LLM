"""Grounded prompt construction (SPEC §5.5): the diff itself plus the
retrieved context (callers, coverage status, incident tags), explicitly
instructing the model to reference specific retrieved facts rather than
generate generic commentary.

`trimmed=True` (SPEC §7's fallback path) drops the lowest-priority
retrieved context first — the semantically-similar-past-code section —
while keeping each function's own callers/test-coverage/incident-tag
facts, since those are the higher-confidence, directly-relevant signal.
"""
from worker.retrieval.query import RetrievalResult

_INSTRUCTIONS = """You are a code review assistant that assesses the STRUCTURAL RISK of a pull request — not style or lint issues, but whether this change touches something fragile that isn't obviously fragile from the diff alone.

You are given, for each changed function: its new code, how many other places call it (and which), whether it has test coverage, and whether it (or code very similar to it) has a documented incident history (a past bug or CVE).

Rules:
- Ground every reason in a SPECIFIC fact given below (a caller name, an incident tag, a "no test coverage" flag, a similar past change). Do not write generic statements that could apply to any diff ("this function looks risky" is not acceptable without saying why).
- If a function has an incident_tags entry, treat that as a strong risk signal and reference the specific tag.
- If a function has zero known callers and no test coverage, say so explicitly — that combination is itself a risk signal (untested, and impact is unclear).
- risk_level must be exactly one of: "low", "medium", "high".
- Respond with ONLY a single JSON object, no prose before or after, matching exactly this shape:
{"risk_level": "low|medium|high", "reasons": ["...", "..."], "suggested_checks": ["...", "..."]}
"""


def _format_incident_tags(tags: list[dict]) -> str:
    if not tags:
        return "none"
    return "; ".join(f'{t["tag"]}: {t["summary"]}' for t in tags)


def _format_function_section(result: RetrievalResult, include_similar: bool) -> str:
    lines = [
        f"### {result.file_path}::{result.function_name}",
        "```",
        result.code,
        "```",
        f"- Already indexed before this change: {result.already_indexed}",
        f"- Known callers ({len(result.callers)}): {', '.join(result.callers) or 'none'}",
        f"- Has test coverage: {result.has_tests}",
        f"- Incident history: {_format_incident_tags(result.incident_tags)}",
    ]

    if include_similar and result.similar:
        lines.append("- Semantically similar existing code:")
        for s in result.similar:
            tag_note = f" [incident: {_format_incident_tags(s.incident_tags)}]" if s.incident_tags else ""
            lines.append(
                f"  - {s.file_path}::{s.function_name} (similarity {s.similarity:.2f}, "
                f"{len(s.callers)} callers, has_tests={s.has_tests}){tag_note}"
            )

    return "\n".join(lines)


def build_prompt(
    owner: str,
    repo_name: str,
    pr_number: int,
    results: list[RetrievalResult],
    trimmed: bool = False,
) -> str:
    """Build the full grounded prompt for a PR's changed functions.

    `results` comes straight from worker.retrieval.query.retrieve_context_for_pr.
    """
    header = f"Pull request: {owner}/{repo_name}#{pr_number}\n{len(results)} changed function(s) below.\n"
    sections = [_format_function_section(r, include_similar=not trimmed) for r in results]
    return "\n\n".join([_INSTRUCTIONS, header, *sections])
