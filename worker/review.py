"""End-to-end PR review pipeline (SPEC F1-F5): ties retrieval (Phase 4),
grounded LLM assessment (SPEC §5.5, §7), and comment posting (SPEC §5.6)
into the single operation the worker performs per accepted PR event.
"""
import logging

from worker.github_client import get_installation_token_for_repo, post_or_update_comment
from worker.llm.assess import assess_pr
from worker.llm.comment_renderer import MARKER, render_comment
from worker.retrieval.query import retrieve_context_for_pr

logger = logging.getLogger(__name__)


def review_pr(owner: str, repo_name: str, pr_number: int, head_sha: str, settings) -> dict:
    """Runs the full pipeline for one PR event and posts/updates the
    comment. `settings` is a config.Settings instance. Returns a small
    stats dict for logging/testing — not meant for direct API exposure.
    """
    with open(settings.github_app_private_key_path) as f:
        private_key = f.read()
    token = get_installation_token_for_repo(settings.github_app_id, private_key, owner, repo_name)

    results = retrieve_context_for_pr(
        owner,
        repo_name,
        pr_number,
        head_sha,
        settings.database_url,
        settings.embedding_model,
        token=token,
    )

    if not results:
        logger.info(
            "No supported-language function changes in %s/%s#%d — skipping review",
            owner,
            repo_name,
            pr_number,
        )
        return {"status": "skipped_no_changes"}

    assessment = assess_pr(
        owner,
        repo_name,
        pr_number,
        results,
        settings.ollama_host,
        settings.ollama_model,
        settings.gemini_api_key,
        settings.gemini_model,
    )

    body = render_comment(assessment, head_sha)
    comment = post_or_update_comment(owner, repo_name, pr_number, body, MARKER, token=token)

    return {
        "status": "reviewed",
        "risk_level": assessment.risk_level,
        "degraded": assessment.degraded,
        "functions_reviewed": len(results),
        "comment_id": comment["id"],
        "comment_url": comment.get("html_url"),
    }
