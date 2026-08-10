"""Structured LLM output shape (SPEC §5.5): risk_level/reasons[]/
suggested_checks[], parsed and rendered into a clean comment — not raw
LLM prose pasted in. Both backends (Ollama, Gemini) target this same
schema, which is what makes them swappable behind a provider-agnostic
interface (SPEC §5.5).
"""
import json
import re
from dataclasses import dataclass, field

RISK_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class Assessment:
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    suggested_checks: list[str] = field(default_factory=list)
    degraded: bool = False  # True only for the "both backends failed" honest-failure path


class AssessmentParseError(ValueError):
    """Raised when a backend's response can't be parsed into a valid
    Assessment — treated as a backend failure by the fallback orchestrator
    (SPEC §7), not surfaced as a crash.
    """


def parse_assessment(raw_text: str) -> Assessment:
    """Extract and validate a JSON object matching the assessment schema
    from a raw LLM response. Tolerant of common small-model quirks: a
    ```json ... ``` fence wrapping the object, or leading/trailing prose
    around it — but not tolerant of a missing/invalid risk_level, since
    silently defaulting that would produce a misleadingly confident
    assessment instead of an honest parse failure.
    """
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        raise AssessmentParseError(f"No JSON object found in response: {raw_text[:200]!r}")

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise AssessmentParseError(f"Malformed JSON in response: {e}") from e

    risk_level = data.get("risk_level")
    if isinstance(risk_level, str):
        risk_level = risk_level.strip().lower()
    if risk_level not in RISK_LEVELS:
        raise AssessmentParseError(f"Invalid or missing risk_level: {risk_level!r}")

    reasons = data.get("reasons", [])
    suggested_checks = data.get("suggested_checks", [])
    if not isinstance(reasons, list) or not isinstance(suggested_checks, list):
        raise AssessmentParseError("reasons/suggested_checks must be arrays")

    return Assessment(
        risk_level=risk_level,
        reasons=[str(r) for r in reasons],
        suggested_checks=[str(c) for c in suggested_checks],
    )
