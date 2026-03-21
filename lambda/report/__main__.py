"""Entry point for the report generator (python -m report)."""

import json
import os
import sys

from . import get_target_date_range, require_env
from .github import GitHubClient
from .notion import NotionClient
from .session import SessionClient
from .slack import SlackClient
from .summarizer import SummaryClient


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
    print(f"Target date range: {since.isoformat()} ~ {until.isoformat()}", file=sys.stderr)

    slack_client = SlackClient(slack_webhook_url)

    try:
        github_client = GitHubClient(github_pat)
        github_activity = github_client.fetch_activity(since, until)
        formatted_github = github_client.format_activity(github_activity)

        session_client = SessionClient(s3_bucket)
        session_activity = session_client.fetch_sessions(since, until, list(github_activity.keys()))
        formatted_sessions = session_client.format_activity(session_activity)

        summary_client = SummaryClient(anthropic_api_key)
        report = summary_client.generate_summary(since, formatted_github, formatted_sessions)

        notion_client = NotionClient(notion_token, notion_db_id)
        pages = notion_client.create_report_pages(
            since, report, github_activity, session_activity,
        )
        for name, url in pages:
            print(f"Created Notion page: {name} -> {url}", file=sys.stderr)

        # Archive processed session logs
        archived = session_client.archive_sessions()
        print(f"Archived {archived} session log(s)", file=sys.stderr)

        # Slack notification (best-effort)
        slack_client.notify(since, report, pages)

        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as e:
        slack_client.notify_error(since, e)
        raise


if __name__ == "__main__":
    main()
