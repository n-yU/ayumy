"""Report generation pipeline."""

import logging
import platform
import resource
import time
from datetime import datetime

from . import JST, date_to_range, get_target_date_range, require_env
from .github import GitHubClient
from .notion import NotionClient
from .session import SessionClient
from .slack import SlackClient
from .store import SessionStore
from .summarizer import SummaryClient

logger = logging.getLogger(__name__)

# Maximum number of past dates to backfill per invocation
MAX_BACKFILL = 3


def process_date(
    since: datetime,
    until: datetime,
    session_client: SessionClient,
    github_client: GitHubClient,
    notion_client: NotionClient,
    summary_client: SummaryClient,
    slack_client: SlackClient,
    allowed_tags: list[str],
    allowed_statuses: list[str],
) -> None:
    """Generate and publish a daily report for a single date range.

    Args:
        since: Start of the target period (inclusive)
        until: End of the target period (exclusive)
        session_client: S3 session log client
        github_client: GitHub API client
        notion_client: Notion API client
        summary_client: Claude API summarizer client
        slack_client: Slack notification client
        allowed_tags: Valid tag names from Notion DB
        allowed_statuses: Valid status names from Notion DB
    """
    logger.info("Processing: %s ~ %s", since.isoformat(), until.isoformat())

    session_activity = session_client.fetch_sessions(since, until)
    github_activity = github_client.fetch_activity(since, until, list(session_activity.keys()))

    if not session_activity and not github_activity:
        logger.info("No activity, skipping")
        return

    report = summary_client.generate_summary(
        since, github_activity.format(), session_activity.format(),
        allowed_tags, allowed_statuses,
    )

    validation = SummaryClient.validate_report(report, allowed_tags, allowed_statuses)
    if validation:
        slack_client.notify_validation_errors(since, validation)

    pages = notion_client.create_report_pages(
        since, report, github_activity, session_activity,
    )
    for name, url in pages:
        logger.info("Created Notion page: %s -> %s", name, url)

    skipped_repos = [
        r["name"] for r in report["repositories"]
        if r["name"] not in github_activity
    ]

    slack_client.notify(since, report, pages, skipped_repos)


def run(source: str | None = None, memory_limit_mb: int | None = None) -> None:
    """Run the report generation pipeline.

    Args:
        source: Invocation source. "manual" for manual execution,
            None for scheduled execution
        memory_limit_mb: Lambda memory limit in MB, or None for CLI
    """
    start = time.monotonic()
    since, until = get_target_date_range(source)
    primary_date = since.astimezone(JST).date()

    slack_client = SlackClient(require_env("SLACK_WEBHOOK_URL"))

    try:
        session_client = SessionClient(require_env("AYUMY_S3_BUCKET"))

        # Ingest all sessions to DynamoDB (no date filtering)
        try:
            store = SessionStore(require_env("AYUMY_DYNAMO_TABLE"))
            ingested = store.ingest(session_client)
            logger.info("Ingested %d session item(s) to DynamoDB", ingested)
        except Exception as e:
            logger.error("DynamoDB ingestion failed: %s", e)
            slack_client.notify_error(since, e)

        # Scan all unarchived files to detect backfill targets
        key_dates = session_client.scan_entry_dates()
        all_dates = set()
        for dates in key_dates.values():
            all_dates.update(dates)

        # Only backfill past dates and pick the most recent MAX_BACKFILL of them
        past_dates = sorted(d for d in all_dates if d < primary_date)
        backfill_dates = past_dates[-MAX_BACKFILL:]
        target_dates = backfill_dates + [primary_date]
        if backfill_dates:
            logger.info("Backfill dates detected: %s", backfill_dates)

        github_client = GitHubClient(require_env("GITHUB_PAT"))
        notion_client = NotionClient(
            require_env("NOTION_SECRET"), require_env("NOTION_DATABASE_ID"),
        )
        allowed_tags, allowed_statuses = notion_client.fetch_allowlists()
        summary_client = SummaryClient(require_env("ANTHROPIC_API_KEY"))

        for target_date in target_dates:
            if target_date == primary_date:
                day_since, day_until = since, until
            else:
                day_since, day_until = date_to_range(target_date)

            # Snapshot key count so we can roll back on failure
            snapshot = session_client.snapshot_keys()
            try:
                process_date(
                    day_since, day_until,
                    session_client, github_client, notion_client,
                    summary_client, slack_client,
                    allowed_tags, allowed_statuses,
                )
            except Exception as e:
                # Roll back keys added by the failed date
                session_client.rollback_keys(snapshot)
                slack_client.notify_error(day_since, e)
                if target_date == primary_date:
                    e._notified = True  # type: ignore[attr-defined]
                    raise

        # Archive only after all dates are processed
        archived = session_client.archive_sessions()
        logger.info("Archived %d session log(s)", archived)

    except Exception as e:
        # Errors from process_date are already notified with the correct date
        # Only notify here for errors outside the loop (scan, allowlists, archive)
        if not getattr(e, "_notified", False):
            slack_client.notify_error(since, e)
        raise
    finally:
        elapsed = time.monotonic() - start
        # macOS returns bytes, Linux returns kilobytes
        ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
        peak_memory_mb = ru_maxrss / divisor
        logger.info(
            "Execution metrics: elapsed=%.1fs, peak_memory=%.0fMB, limit=%s",
            elapsed, peak_memory_mb, memory_limit_mb,
        )
        slack_client.notify_metrics(elapsed, peak_memory_mb, memory_limit_mb)
        slack_client.flush()

