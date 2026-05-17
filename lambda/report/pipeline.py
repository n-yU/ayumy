"""Report generation pipeline."""

import logging
import platform
import resource
import time
from datetime import datetime

from . import (
    JST, SessionActivity, date_to_range, get_target_date_range, get_version,
    parse_target_dates, require_env,
)
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
    session_activity: SessionActivity,
    github_client: GitHubClient,
    notion_client: NotionClient,
    summary_client: SummaryClient,
    slack_client: SlackClient,
    *,
    is_backfill: bool = False,
) -> None:
    """Generate and publish a daily report for a single date range.

    Args:
        since: Start of the target period (inclusive)
        until: End of the target period (exclusive)
        session_activity: Pre-fetched session data from DynamoDB
        github_client: GitHub API client
        notion_client: Notion API client
        summary_client: Claude API summarizer client (already configured
            with the tag allowlist)
        slack_client: Slack notification client
        is_backfill: If True, use the Hybrid PR/Issue fetch path
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
        since, until, list(session_activity.keys()),
        is_backfill=is_backfill,
        session_pulls=session_pulls,
        session_issues=session_issues,
    )

    # Recover squash-merged commits from session logs, normalizing to
    # the full CommitInfo shape and deduplicating by SHA prefix.
    for repo_name, sessions in session_activity.repos().items():
        seen_shas: set[str] = set()
        session_commits: list[dict] = []
        for s in sessions:
            for c in s.get("session_commits", []):
                if c["sha"] in seen_shas:
                    continue
                seen_shas.add(c["sha"])
                session_commits.append({
                    "sha": c["sha"],
                    "message": c["message"],
                    "author": "",
                    "date": c.get("timestamp") or s["start_time"],
                    "url": f"https://github.com/{github_client.owner}/{repo_name}/commit/{c['sha']}",
                    "pull_numbers": [],
                })
        if not session_commits:
            continue
        # Resolve PR association on commits we will actually inject so these
        # nest under their parent PR in the Notion timeline rather than
        # render as direct commits. Skip ones already covered by search.
        repo_data = github_activity.repos().get(repo_name)
        if repo_data is not None:
            existing_shas = {c["sha"] for c in repo_data["commits"]}
            new_commits = [
                sc for sc in session_commits
                if not any(s.startswith(sc["sha"]) for s in existing_shas)
            ]
            if new_commits:
                github_client.populate_commit_pull_numbers(repo_name, new_commits)
                repo_data["commits"].extend(new_commits)
        else:
            github_client.populate_commit_pull_numbers(repo_name, session_commits)
            github_activity.repos()[repo_name] = {
                "commits": session_commits,
                "pulls": [],
                "issues": [],
            }

    if not session_activity and not github_activity:
        logger.info("No activity, skipping")
        slack_client.notify_no_activity(since)
        return

    report = summary_client.generate_summary(
        since, github_activity.format(), session_activity.format(),
    )

    validation = summary_client.validate_report(report)
    if validation:
        slack_client.notify_validation_errors(since, validation)

    pages = notion_client.create_report_pages(
        since, since, until, report, github_activity, session_activity,
    )
    for name, url in pages:
        logger.info("Created Notion page: %s -> %s", name, url)

    skipped_repos = [
        r["name"] for r in report["repositories"]
        if r["name"] not in github_activity
    ]

    slack_client.notify(since, report, pages, skipped_repos)


def run(
    source: str | None = None,
    target_date: str | None = None,
    memory_limit_mb: int | None = None,
    timeout_seconds: int | None = None,
) -> None:
    """Run the report generation pipeline.

    Args:
        source: Invocation source. "manual" for manual execution,
            None for scheduled execution
        target_date: Explicit target date (YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD)
            for report generation
        memory_limit_mb: Lambda memory limit in MB, or None for CLI
        timeout_seconds: Lambda timeout in seconds, or None for CLI
    """
    start = time.monotonic()
    since, until = get_target_date_range(source, target_date=target_date)
    primary_date = since.astimezone(JST).date()

    slack_client = SlackClient(require_env("SLACK_WEBHOOK_URL"))

    try:
        session_client = SessionClient(require_env("AYUMY_S3_BUCKET"))
        store = SessionStore(require_env("AYUMY_DYNAMO_TABLE"))

        # Ingest JSONL to DynamoDB and delete from S3
        ingested_keys: list[str] = []
        try:
            ingested_keys = store.ingest(session_client)
            logger.info("Ingested %d JSONL file(s)", len(ingested_keys))
        except Exception as e:
            logger.exception("DynamoDB ingestion failed")
            slack_client.notify_error(since, e)

        if ingested_keys:
            try:
                deleted = session_client.delete_sessions(ingested_keys)
                logger.info("Deleted %d JSONL file(s) from S3", deleted)
            except Exception as e:
                logger.exception("S3 deletion failed")
                slack_client.notify_error(since, e)

        # Build target date list and mark which ones use the Hybrid fetch path
        backfill_set: set[str] = set()
        if target_date:
            # Explicit date(s): process only specified dates, skip backfill
            process_dates = parse_target_dates(target_date)
            backfill_set = {d.isoformat() for d in process_dates}
        else:
            # Scheduled/manual without --date: backfill + primary
            backfill_dates = store.scan_backfill_dates(primary_date)
            backfill_dates = backfill_dates[-MAX_BACKFILL:]
            process_dates = backfill_dates + [primary_date]
            backfill_set = {d.isoformat() for d in backfill_dates}
            if backfill_dates:
                logger.info("Backfill dates detected: %s", backfill_dates)

        github_client = GitHubClient(require_env("GITHUB_PAT"))
        notion_client = NotionClient(
            require_env("NOTION_SECRET"), require_env("NOTION_DATABASE_ID"),
        )
        allowed_tags = notion_client.fetch_allowlists()
        summary_client = SummaryClient(
            require_env("ANTHROPIC_API_KEY"), allowed_tags,
        )

        errors: list[Exception] = []
        for d in process_dates:
            if not target_date and d == primary_date:
                day_since, day_until = since, until
            else:
                day_since, day_until = date_to_range(d)
            date_str = d.isoformat()
            try:
                session_activity = store.fetch_sessions(date_str)
                process_date(
                    day_since, day_until, session_activity,
                    github_client, notion_client,
                    summary_client, slack_client,
                    is_backfill=date_str in backfill_set,
                )
                store.mark_reported(date_str)
            except Exception as e:
                slack_client.notify_error(day_since, e)
                e._notified = True  # type: ignore[attr-defined]
                if not target_date and d == primary_date:
                    raise
                if target_date:
                    errors.append(e)

        if errors:
            raise errors[0]

    except Exception as e:
        # Errors from process_date are already notified with the correct date
        # Only notify here for errors outside the loop (scan, allowlists)
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
            elapsed, peak_memory_mb, memory_limit_mb, timeout_seconds,
        )
        slack_client.notify_metrics(
            elapsed, peak_memory_mb, get_version(),
            memory_limit_mb=memory_limit_mb,
            timeout_seconds=timeout_seconds,
        )
        slack_client.flush()
