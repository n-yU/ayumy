"""Report generation pipeline."""

import logging
import platform
import resource
import time
from datetime import datetime

from botocore.exceptions import BotoCoreError, ClientError

from config import CONFIG

from . import (
    JST,
    SessionActivity,
    date_to_range,
    get_target_date_range,
    get_version,
    parse_target_dates,
    require_env,
)
from .cost import CostDisplay, CostStore
from .github import GitHubClient
from .notice import Notice, NoticeSource
from .notion import NotionClient
from .session import SessionClient, SessionStore
from .slack import SlackClient
from .summarizer import SummaryClient

logger = logging.getLogger(__name__)


def process_date(
    since: datetime,
    until: datetime,
    session_activity: SessionActivity,
    github_client: GitHubClient,
    notion_client: NotionClient,
    summary_client: SummaryClient,
    cost_store: CostStore,
    slack_client: SlackClient,
    *,
    is_backfill: bool = False,
) -> None:
    """Generate and publish the report for a single date window.

    When `is_backfill=True`, switches to Hybrid PR/Issue fetch (Spec: Hybrid Backfill Fetch) to recover items whose `updated_at` has drifted out of the window,
    and marks the Slack report header as a backfill.
    """
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
        slack_client.notify_no_activity(since, is_backfill=is_backfill)
        return

    session_only_repos = sorted(
        r for r in session_activity.keys() if r not in github_activity
    )
    effective_sessions = (
        session_activity.without(session_only_repos)
        if session_only_repos
        else session_activity
    )

    if not github_activity and not effective_sessions:
        logger.info(
            "All repos are session-only, skipping Claude summary: %s",
            session_only_repos,
        )
        slack_client.notify_session_only(
            since, session_only_repos, is_backfill=is_backfill
        )
        return

    report, usage = summary_client.generate_summary(
        since,
        github_activity.format(since, until),
        effective_sessions.format(),
    )
    logger.info(
        "Claude API usage: input=%d, output=%d, spend=%.6f USD",
        usage.input_tokens,
        usage.output_tokens,
        usage.spend_usd,
    )

    cost_store.start_record(since.date(), usage)

    validation = summary_client.validate_report(report)
    if validation:
        slack_client.notify_validation_errors(
            since, validation, is_backfill=is_backfill
        )

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

    slack_client.notify(
        since,
        report,
        pages,
        session_only_repos=session_only_repos or None,
        is_backfill=is_backfill,
    )


def run(
    source: str | None = None,
    target_date: str | None = None,
    memory_limit_mb: int | None = None,
    timeout_seconds: int | None = None,
) -> None:
    """Run the report generation for every date this invocation covers.

    `target_date` (`YYYY-MM-DD` or `YYYY-MM-DD..YYYY-MM-DD`) takes precedence and processes every given date as a backfill;
    otherwise processes the prior day (scheduled) or today's partial window (`source="manual"`) plus unreported backfill dates.
    `memory_limit_mb` / `timeout_seconds` are None from the CLI.
    """
    start = time.monotonic()
    since, until = get_target_date_range(source, target_date=target_date)
    primary_date = since.astimezone(JST).date()

    slack_client = SlackClient(
        token=require_env("SLACK_BOT_TOKEN"),
        channel=require_env("SLACK_CHANNEL"),
        is_manual=source == "manual",
    )
    notice = Notice()
    cost_store: CostStore | None = None

    try:
        session_client = SessionClient(require_env("AYUMY_S3_BUCKET"), notice=notice)
        store = SessionStore(require_env("AYUMY_DYNAMO_TABLE"), notice=notice)

        ingested_keys = store.ingest(session_client)
        logger.info("Ingested %d JSONL file(s)", len(ingested_keys))

        if ingested_keys:
            try:
                deleted = session_client.delete_sessions(ingested_keys)
                logger.info("Deleted %d JSONL file(s) from S3", deleted)
            except (ClientError, BotoCoreError):
                notice.add(
                    NoticeSource.PIPELINE,
                    "S3 deletion failed; JSONL will be re-ingested on next run",
                    logger=logger,
                    exc_info=True,
                )

        backfill_set: set[str] = set()
        if target_date:
            process_dates = parse_target_dates(target_date)
            backfill_set = {d.isoformat() for d in process_dates}
        else:
            backfill_dates = store.scan_backfill_dates(primary_date)
            backfill_dates = backfill_dates[-CONFIG.pipeline.max_backfill :]
            process_dates = backfill_dates + [primary_date]
            backfill_set = {d.isoformat() for d in backfill_dates}
            if backfill_dates:
                logger.info("Backfill dates detected: %s", backfill_dates)

        github_client = GitHubClient(require_env("GITHUB_PAT"), notice=notice)
        notion_client = NotionClient(
            require_env("NOTION_SECRET"),
            require_env("NOTION_DATABASE_ID"),
            github_client.owner,
            notice=notice,
        )
        notion_client.init_data_source()
        summary_client = SummaryClient(require_env("ANTHROPIC_API_KEY"), notice=notice)
        cost_store = CostStore(require_env("AYUMY_COST_TABLE"))

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
                    cost_store,
                    slack_client,
                    is_backfill=date_str in backfill_set,
                )
                store.mark_reported(date_str)
            except Exception as e:
                # Broad: pipeline loop classifies per-day failure into error or warning
                if not target_date and d == primary_date:
                    logger.exception("Report generation failed for %s", date_str)
                    slack_client.notify_error(day_since, e)
                    e._notified = True  # type: ignore[attr-defined]
                    raise
                notice.add(
                    NoticeSource.PIPELINE,
                    "Report generation failed",
                    logger=logger,
                    exc_info=True,
                    date=date_str,
                    error=repr(e),
                )

    except Exception as e:
        # Broad: pipeline final fallback, ensures any uncaught failure reaches Slack
        if not getattr(e, "_notified", False):
            # Every explicitly requested date is processed as a backfill, and `since` is the first of them
            slack_client.notify_error(since, e, is_backfill=bool(target_date))
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
        cost_display: CostDisplay | None = None
        if cost_store is not None:
            try:
                cost_display = cost_store.compute_display(datetime.now(JST).date())
            except Exception:
                # Broad: cost display is auxiliary; any failure here should not block metrics/notice notifications
                notice.add(
                    NoticeSource.PIPELINE,
                    "Cost display computation failed; cost line omitted",
                    logger=logger,
                    exc_info=True,
                )
        slack_client.notify_metrics(
            elapsed,
            peak_memory_mb,
            get_version(),
            cost=cost_display,
            memory_limit_mb=memory_limit_mb,
            timeout_seconds=timeout_seconds,
        )
        slack_client.flush()
        slack_client.send_notice_thread(notice)
