"""Entry point for the report generator (python -m report)."""

import json
import os
import sys

from . import get_target_date_range
from .github import GitHubClient
from .session import SessionClient
from .summarizer import SummaryClient


def main() -> None:
    """Entry point for the report generator."""
    github_pat = os.environ.get("GITHUB_PAT")
    if not github_pat:
        print("GITHUB_PAT is not set", file=sys.stderr)
        sys.exit(1)

    s3_bucket = os.environ.get("AYUMY_S3_BUCKET")
    if not s3_bucket:
        print("AYUMY_S3_BUCKET is not set", file=sys.stderr)
        sys.exit(1)

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    source = os.environ.get("AYUMY_SOURCE")
    since, until = get_target_date_range(source)
    print(f"Target date range: {since.isoformat()} ~ {until.isoformat()}", file=sys.stderr)

    github_client = GitHubClient(github_pat)
    github_activity = github_client.fetch_activity(since, until)
    formatted_github = github_client.format_activity(github_activity)

    session_client = SessionClient(s3_bucket)
    session_activity = session_client.fetch_sessions(since, until)
    formatted_sessions = session_client.format_activity(session_activity)

    summary_client = SummaryClient(anthropic_api_key)
    report = summary_client.generate_summary(since, formatted_github, formatted_sessions)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
