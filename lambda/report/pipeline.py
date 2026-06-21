"""Report generation pipeline."""

import logging
import platform
import resource
import time
from datetime import datetime

from botocore.exceptions import BotoCoreError, ClientError

from . import (
    JST,
    SessionActivity,
    date_to_range,
    get_target_date_range,
    get_version,
    parse_target_dates,
    require_env,
)
from .github import GitHubClient
from .notion import NotionClient
from .session import SessionClient, SessionStore
from .slack import SlackClient
from .summarizer import SummaryClient

logger = logging.getLogger(__name__)

MAX_BACKFILL = 3


def process_date(
    since: datetime,
    until: datetime,
    session_activity: SessionActivity,
    github_client: GitHubClient,
    notion_client: NotionClient,
    summary_client: SummaryClient,
    slack_client: SlackClient,
    *,
    is_backfill: bool = False,
) -> None:
    """When `is_backfill=True`, switches to Hybrid PR/Issue fetch (Spec: Hybrid Backfill Fetch) to recover items whose `updated_at` has drifted out of the window."""
    logger.info("Processing: %s ~ %s", since.isoformat(), until.isoformat())

    session_pulls: dict[str, list[int]] = {}
    session_issues: dict[str, list[int]] = {}
    if is_backfill:
        for repo_name, sessions in session_activity.repos().items():
            pulls: set[int] = set()
            issues: set[int] = set()
            for s in sessions:
                pulls.update(s.get("session_pulls", []))
                issues.update(s.get("session_issues", []))
            if pulls:
                session_pulls[repo_name] = sorted(pulls)
            if issues:
                session_issues[repo_name] = sorted(issues)

    github_activity = github_client.fetch_activity(
        since,
        until,
        list(session_activity.keys()),
        is_backfill=is_backfill,
        session_pulls=session_pulls,
        session_issues=session_issues,
    )

    for repo_name, sessions in session_activity.repos().items():
        github_activity.merge_session_commits(
            repo_name,
            sessions,
            owner=github_client.owner,
            populate_pull_numbers=github_client.populate_commit_pull_numbers,
        )

    if not session_activity and not github_activity:
        logger.info("No activity, skipping")
        slack_client.notify_no_activity(since)
        return

    report = summary_client.generate_summary(
        since,
        github_activity.format(),
        session_activity.format(),
    )

    validation = summary_client.validate_report(report)
    if validation:
        slack_client.notify_validation_errors(since, validation)

    pages = notion_client.create_report_pages(
        since,
        since,
        until,
        report,
        github_activity,
        session_activity,
    )
    for name, url in pages:
        logger.info("Created Notion page: %s -> %s", name, url)

    skipped_repos = [
        r["name"] for r in report["repositories"] if r["name"] not in github_activity
    ]

    slack_client.notify(since, report, pages, skipped_repos)


def run(
    source: str | None = None,
    target_date: str | None = None,
    memory_limit_mb: int | None = None,
    timeout_seconds: int | None = None,
) -> None:
    """`target_date` (`YYYY-MM-DD` or `YYYY-MM-DD..YYYY-MM-DD`) takes precedence and disables backfill;
    otherwise processes the prior day (scheduled) or today's partial window (`source="manual"`) plus unreported backfill dates.
    `memory_limit_mb` / `timeout_seconds` are None from the CLI.
    """
    start = time.monotonic()
    since, until = get_target_date_range(source, target_date=target_date)
    primary_date = since.astimezone(JST).date()

    slack_client = SlackClient(
        token=require_env("SLACK_BOT_TOKEN"),
        channel=require_env("SLACK_CHANNEL"),
    )

    try:
        session_client = SessionClient(require_env("AYUMY_S3_BUCKET"))
        store = SessionStore(require_env("AYUMY_DYNAMO_TABLE"))

        ingested_keys = store.ingest(session_client)
        logger.info("Ingested %d JSONL file(s)", len(ingested_keys))

        if ingested_keys:
            try:
                deleted = session_client.delete_sessions(ingested_keys)
                logger.info("Deleted %d JSONL file(s) from S3", deleted)
            except (ClientError, BotoCoreError):
                logger.warning(
                    "S3 deletion failed; JSONL will be re-ingested on next run",
                    exc_info=True,
                )

        backfill_set: set[str] = set()
        if target_date:
            process_dates = parse_target_dates(target_date)
            backfill_set = {d.isoformat() for d in process_dates}
        else:
            backfill_dates = store.scan_backfill_dates(primary_date)
            backfill_dates = backfill_dates[-MAX_BACKFILL:]
            process_dates = backfill_dates + [primary_date]
            backfill_set = {d.isoformat() for d in backfill_dates}
            if backfill_dates:
                logger.info("Backfill dates detected: %s", backfill_dates)

        github_client = GitHubClient(require_env("GITHUB_PAT"))
        notion_client = NotionClient(
            require_env("NOTION_SECRET"),
            require_env("NOTION_DATABASE_ID"),
        )
        notion_client.init_data_source()
        summary_client = SummaryClient(require_env("ANTHROPIC_API_KEY"))

        for d in process_dates:
            if not target_date and d == primary_date:
                day_since, day_until = since, until
            else:
                day_since, day_until = date_to_range(d)
            date_str = d.isoformat()
            try:
                session_activity = store.fetch_sessions(date_str)
                process_date(
                    day_since,
                    day_until,
                    session_activity,
                    github_client,
                    notion_client,
                    summary_client,
                    slack_client,
                    is_backfill=date_str in backfill_set,
                )
                store.mark_reported(date_str)
            except Exception as e:
                # Broad: pipeline loop classifies per-day failure into error or warning
                if not target_date and d == primary_date:
                    slack_client.notify_error(day_since, e)
                    e._notified = True  # type: ignore[attr-defined]
                    raise
                logger.warning("Report generation failed for %s: %r", date_str, e)

    except Exception as e:
        # Broad: pipeline final fallback, ensures any uncaught failure reaches Slack
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
            "Execution metrics: elapsed=%.1fs, peak_memory=%.0fMB, "
            "memory_limit=%s, timeout=%s",
            elapsed,
            peak_memory_mb,
            memory_limit_mb,
            timeout_seconds,
        )
        slack_client.notify_metrics(
            elapsed,
            peak_memory_mb,
            get_version(),
            memory_limit_mb=memory_limit_mb,
            timeout_seconds=timeout_seconds,
        )
        slack_client.flush()
