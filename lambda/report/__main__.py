"""Entry point for the report generator (python -m report)."""

import json
import os
import sys
from datetime import datetime

from . import JST, date_to_range, get_target_date_range, require_env
from .github import GitHubClient
from .notion import NotionClient
from .session import SessionClient
from .slack import SlackClient
from .summarizer import SummaryClient

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
    print(f"Processing: {since.isoformat()} ~ {until.isoformat()}", file=sys.stderr)

    session_activity = session_client.fetch_sessions(since, until)
    github_activity = github_client.fetch_activity(since, until, list(session_activity.keys()))

    if not session_activity and not github_activity:
        print("  No activity, skipping", file=sys.stderr)
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
        print(f"  Created Notion page: {name} -> {url}", file=sys.stderr)

    skipped_repos = [
        r["name"] for r in report["repositories"]
        if r["name"] not in github_activity
    ]

    slack_client.notify(since, report, pages, skipped_repos)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    """Entry point for the report generator."""
    github_pat = require_env("GITHUB_PAT")
    s3_bucket = require_env("AYUMY_S3_BUCKET")
    anthropic_api_key = require_env("ANTHROPIC_API_KEY")
    notion_token = require_env("NOTION_SECRET")
    notion_db_id = require_env("NOTION_DATABASE_ID")
    slack_webhook_url = require_env("SLACK_WEBHOOK_URL")

    source = os.environ.get("AYUMY_SOURCE")
    since, until = get_target_date_range(source)
    primary_date = since.astimezone(JST).date()

    slack_client = SlackClient(slack_webhook_url)

    try:
        session_client = SessionClient(s3_bucket)

        # Scan all unarchived files to detect backfill targets
        key_dates = session_client.scan_entry_dates()
        all_dates = set()
        for dates in key_dates.values():
            all_dates.update(dates)

        backfill_dates = sorted(d for d in all_dates if d != primary_date)[:MAX_BACKFILL]
        target_dates = backfill_dates + [primary_date]
        if backfill_dates:
            print(f"Backfill dates detected: {backfill_dates}", file=sys.stderr)

        github_client = GitHubClient(github_pat)
        notion_client = NotionClient(notion_token, notion_db_id)
        allowed_tags, allowed_statuses = notion_client.fetch_allowlists()
        summary_client = SummaryClient(anthropic_api_key)

        for target_date in target_dates:
            if target_date == primary_date:
                day_since, day_until = since, until
            else:
                day_since, day_until = date_to_range(target_date)

            process_date(
                day_since, day_until,
                session_client, github_client, notion_client,
                summary_client, slack_client,
                allowed_tags, allowed_statuses,
            )

        # Archive only after all dates are processed
        archived = session_client.archive_sessions()
        print(f"Archived {archived} session log(s)", file=sys.stderr)

    except Exception as e:
        slack_client.notify_error(since, e)
        raise


if __name__ == "__main__":
    main()
