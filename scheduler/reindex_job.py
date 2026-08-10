"""Scheduled embedding-refresh job (SPEC §5.1b, N5).

Decision (SPEC §1): a scheduled job, not a push webhook — independent of
the webhook receiver, no second HMAC verification path. Runs every
REINDEX_INTERVAL_MINUTES (default 15, matching N5's staleness bound) and
re-indexes each configured repo via worker.retrieval.indexer.index_repo,
which itself diffs against the last-indexed SHA so only changed functions
get re-embedded.
"""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from config import get_settings
from worker.github_client import get_installation_token_for_repo
from worker.retrieval.indexer import index_repo

logger = logging.getLogger(__name__)


def _token_for_repo(settings, owner: str, name: str) -> str | None:
    """Best-effort: authenticate as the GitHub App's installation on this
    repo if it's installed there (raises the API rate limit from 60/hr to
    5000/hr — matters for the demo-target repo, which sees every file
    fetched on every full/incremental index). Falls back to unauthenticated
    access for repos without the App installed (e.g. the public eval repo).
    """
    if not settings.github_app_id:
        return None
    try:
        with open(settings.github_app_private_key_path) as f:
            private_key = f.read()
    except OSError:
        return None
    return get_installation_token_for_repo(settings.github_app_id, private_key, owner, name)


def run_once(force_full: bool = False) -> list[dict]:
    """Reindex every repo in REINDEX_REPOS once. Returns one stats dict per
    repo. A failure on one repo (e.g. a transient GitHub rate limit) is
    logged and does not prevent the others from being attempted — but it
    also does not advance that repo's last-indexed SHA (see indexer.py),
    so the next tick retries it cleanly.
    """
    settings = get_settings()
    results = []
    for repo in settings.reindex_repos:
        owner, name = repo.split("/", 1)
        try:
            token = _token_for_repo(settings, owner, name)
            result = index_repo(
                repo=repo,
                owner=owner,
                name=name,
                database_url=settings.database_url,
                embedding_model=settings.embedding_model,
                token=token,
                force_full=force_full,
            )
            logger.info("Reindexed %s: %s", repo, result)
            results.append({"repo": repo, **result})
        except Exception:
            logger.exception("Reindex failed for %s — will retry next tick", repo)
            results.append({"repo": repo, "status": "failed"})
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    # Run once immediately on startup so a fresh deployment doesn't wait a
    # full interval before the index has anything in it.
    run_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_once,
        "interval",
        minutes=settings.reindex_interval_minutes,
        id="reindex",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Reindex scheduler started: every %d min", settings.reindex_interval_minutes)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        logging.basicConfig(level=logging.INFO)
        print(run_once(force_full="--force-full" in sys.argv))
    else:
        main()
