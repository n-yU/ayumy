"""Ayumy daily report generator.

Fetches GitHub activity and Claude Code session logs for the target
date range and formats them for downstream processing.
"""

import os
from collections.abc import KeysView
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import NotRequired, TypedDict

JST = timezone(timedelta(hours=9))


class CommitInfo(TypedDict):
    sha: str
    message: str
    author: str
    date: str
    url: str
    pull_numbers: list[int]


class SessionCommit(TypedDict):
    """A commit recovered from Claude Code session logs.

    `timestamp` may be missing on legacy DynamoDB entries; pipeline
    code falls back to the session's start_time and normalizes the
    entry to CommitInfo when injecting into GitHubActivity.
    """
    sha: str
    message: str
    timestamp: NotRequired[str]


class PullInfo(TypedDict):
    number: int
    title: str
    state: str
    author: str
    labels: list[str]
    draft: bool
    url: str
    created_at: str
    merged_at: str | None
    closed_at: str | None


class IssueInfo(TypedDict):
    number: int
    title: str
    state: str
    author: str
    labels: list[str]
    url: str
    created_at: str
    closed_at: str | None
    state_reason: str | None


class RepoActivity(TypedDict):
    commits: list[CommitInfo]
    pulls: list[PullInfo]
    issues: list[IssueInfo]


class GitHubActivity:
    """GitHub activity data keyed by repository name."""

    def __init__(self, data: dict[str, RepoActivity]) -> None:
        self._data = data

    def repos(self) -> dict[str, RepoActivity]:
        """Return the underlying repo-keyed dict."""
        return self._data

    def keys(self) -> KeysView[str]:
        """Return repository names."""
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def format(self) -> str:
        """Format GitHub activity into Markdown for Claude API input.

        Returns:
            A Markdown-formatted string for the "# GitHub アクティビティ"
            section, suitable for inclusion in the Spec.md §5.3 input format
        """
        if not self._data:
            return "# GitHub アクティビティ\nアクティビティなし"

        lines = ["# GitHub アクティビティ"]
        for repo_name, data in sorted(self._data.items()):
            lines.append(f"## {repo_name}")

            if data["commits"]:
                lines.append("### Commits")
                for c in data["commits"]:
                    lines.append(f"- {c['message']}")

            if data["pulls"]:
                lines.append("### Pull Requests")
                for pr in data["pulls"]:
                    labels = f" ({', '.join(pr['labels'])})" if pr["labels"] else ""
                    lines.append(f"- [{pr['state']}] #{pr['number']} {pr['title']}{labels}")

            if data["issues"]:
                lines.append("### Issues")
                for issue in data["issues"]:
                    labels = f" ({', '.join(issue['labels'])})" if issue["labels"] else ""
                    lines.append(f"- [{issue['state']}] #{issue['number']} {issue['title']}{labels}")

        return "\n".join(lines)


class SessionInfo(TypedDict):
    session_id: str
    project: str
    start_time: str
    end_time: str
    user_messages: list[str]
    tools_used: list[str]
    session_commits: list[SessionCommit]
    session_pulls: list[int]
    session_issues: list[int]


class SessionActivity:
    """Claude Code session activity data keyed by repository name."""

    def __init__(self, data: dict[str, list[SessionInfo]]) -> None:
        self._data = data

    def repos(self) -> dict[str, list[SessionInfo]]:
        """Return the underlying repo-keyed dict."""
        return self._data

    def keys(self) -> KeysView[str]:
        """Return repository/project names."""
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: list[SessionInfo] | None = None) -> list[SessionInfo] | None:
        """Get sessions for a repo, with optional default."""
        return self._data.get(key, default)

    def format(self) -> str:
        """Format session logs into Markdown for Claude API input.

        Returns:
            A Markdown-formatted string for the "# Claude Code セッション"
            section, suitable for inclusion in the Spec.md §5.3 input format
        """
        if not self._data:
            return "# Claude Code セッション\nセッションなし"

        lines = ["# Claude Code セッション"]
        for project_name, sessions in sorted(self._data.items()):
            lines.append(f"## プロジェクト: {project_name}")

            for i, session in enumerate(sessions, 1):
                start = self._format_time(session["start_time"])
                end = self._format_time(session["end_time"])
                lines.append(f"### セッション {i} ({start} - {end})")

                for msg in session["user_messages"]:
                    lines.append(f"- ユーザー: {msg}")

                if session["tools_used"]:
                    tools = ", ".join(session["tools_used"])
                    lines.append(f"- ツール使用: {tools}")

        return "\n".join(lines)

    @staticmethod
    def _format_time(iso_timestamp: str) -> str:
        """Convert an ISO timestamp to JST HH:MM format.

        Args:
            iso_timestamp: ISO 8601 timestamp string

        Returns:
            Time string in "HH:MM" format (JST)
        """
        if not iso_timestamp:
            return "??:??"
        dt = datetime.fromisoformat(iso_timestamp).astimezone(JST)
        return dt.strftime("%H:%M")


class RepoSummary(TypedDict):
    name: str
    summary: list[str]
    tags: list[str]


class ReportSummary(TypedDict):
    repositories: list[RepoSummary]


def require_env(name: str) -> str:
    """Get a required environment variable or raise an error.

    Args:
        name: Environment variable name

    Returns:
        The environment variable value

    Raises:
        ValueError: If the environment variable is not set
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value


@lru_cache
def get_version() -> str:
    """Read the ayumy version from the VERSION file.

    Returns:
        The version string (e.g. "0.1.0")
    """
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    return version_path.read_text().strip()


def date_to_range(target: date) -> tuple[datetime, datetime]:
    """Convert a date to a full JST day range.

    Args:
        target: The target date

    Returns:
        A tuple of (since, until) covering 00:00 JST to next day 00:00 JST
    """
    since = datetime(target.year, target.month, target.day, tzinfo=JST)
    until = since + timedelta(days=1)
    return since, until


def parse_target_dates(target_date: str) -> list[date]:
    """Parse a target date string into a list of dates.

    Supports single date (YYYY-MM-DD) and range (YYYY-MM-DD..YYYY-MM-DD).

    Args:
        target_date: Date string in YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD format

    Returns:
        A list of date objects (inclusive on both ends)

    Raises:
        ValueError: If the start date is after the end date, or range exceeds
            31 days
    """
    MAX_RANGE_DAYS = 31

    if ".." in target_date:
        start_str, end_str = target_date.split("..", 1)
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
        if start > end:
            raise ValueError(f"Start date {start} is after end date {end}")
        if (end - start).days >= MAX_RANGE_DAYS:
            raise ValueError(
                f"Date range exceeds {MAX_RANGE_DAYS} days: {start}..{end}"
            )
        dates = []
        current = start
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
        return dates
    return [date.fromisoformat(target_date)]


def get_target_date_range(
    source: str | None = None,
    target_date: str | None = None,
) -> tuple[datetime, datetime]:
    """Return the target date range for activity fetching.

    Args:
        source: Invocation source. "manual" for manual execution,
            None or other values for scheduled execution
        target_date: Explicit target date (YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD).
            When specified, returns JST 00:00 ~ next day JST 00:00 for the
            first date in the string

    Returns:
        A tuple of (since, until) as timezone-aware datetime objects.
        target_date specified: first date JST 00:00 ~ next day JST 00:00.
        Scheduled: previous day JST 00:00 ~ today JST 00:00.
        Manual: today JST 00:00 ~ now
    """
    if target_date:
        first = target_date.split("..")[0]
        return date_to_range(date.fromisoformat(first))

    now_jst = datetime.now(JST)
    today_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    if source == "manual":
        return today_jst, now_jst

    yesterday_jst = today_jst - timedelta(days=1)
    return yesterday_jst, today_jst
