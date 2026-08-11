"""Retry-with-fallback-then-degrade LLM orchestration (SPEC §7):
1. Attempt Ollama, bounded timeout (N4, 30s).
2. On timeout/error/unparseable response, retry once against the hosted
   fallback with a trimmed prompt (SPEC §5.5: drop lower-priority context
   — the semantic-neighbor section — first).
3. If the fallback also fails, return an honest degraded Assessment rather
   than raising or silently dropping the PR — a silent failure looks like
   "reviewed, nothing found," which is actively misleading (SPEC §7).

A response that comes back but fails schema validation (AssessmentParseError)
is treated the same as a network/timeout failure — it's equally unusable,
just for a different reason.

SPEC §7 amendment (2026-08-11): the Phase 6 eval measured Ollama timing out
on 3/12 real PRs, all multi-changed-function ones — the untrimmed prompt
runs large enough on this hardware to push generation past the 30s budget.
The *first* Ollama attempt is now also trimmed when a PR has more than one
changed function, not just the fallback attempt — single-function PRs
(never the ones timing out) still get the full prompt on the first try.
"""
import logging

from worker.llm import hosted_client, ollama_client
from worker.llm.prompt_builder import build_prompt
from worker.llm.schema import Assessment, parse_assessment
from worker.retrieval.query import RetrievalResult

logger = logging.getLogger(__name__)

DEGRADED_MESSAGE = "Automated risk analysis unavailable for this PR — both the primary and fallback LLM backends failed."


def assess_pr(
    owner: str,
    repo_name: str,
    pr_number: int,
    results: list[RetrievalResult],
    ollama_host: str,
    ollama_model: str,
    gemini_api_key: str,
    gemini_model: str,
) -> Assessment:
    # Multi-function PRs produce large enough prompts to risk the 30s N4
    # timeout on this hardware (see module docstring) -- trim the first
    # attempt too in that case, not just the fallback.
    primary_prompt = build_prompt(owner, repo_name, pr_number, results, trimmed=len(results) > 1)

    try:
        raw = ollama_client.generate(primary_prompt, ollama_host, ollama_model)
        return parse_assessment(raw)
    except Exception:
        logger.warning(
            "Ollama backend failed for %s/%s#%d — retrying against hosted fallback",
            owner,
            repo_name,
            pr_number,
            exc_info=True,
        )

    trimmed_prompt = build_prompt(owner, repo_name, pr_number, results, trimmed=True)
    try:
        raw = hosted_client.generate(trimmed_prompt, gemini_api_key, gemini_model)
        return parse_assessment(raw)
    except Exception:
        logger.error(
            "Hosted fallback also failed for %s/%s#%d — degrading honestly",
            owner,
            repo_name,
            pr_number,
            exc_info=True,
        )

    return Assessment(
        # "medium" is a deliberate "we genuinely don't know, use human
        # judgment" default — "low" would be misleadingly reassuring,
        # "high" would be needlessly alarmist for a PR nothing was
        # actually found wrong with.
        risk_level="medium",
        reasons=[DEGRADED_MESSAGE],
        suggested_checks=["Manual review recommended — automated analysis was unavailable."],
        degraded=True,
    )
