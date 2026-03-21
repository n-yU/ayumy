"""Ayumy daily report generator.

Fetches GitHub activity and Claude Code session logs for the target
date range and formats them for downstream processing.
"""

import os
import sys
from collections.abc import KeysView
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

JST = timezone(timedelta(hours=9))

# Type aliases for structured activity data
CommitInfo = dict[str, str]
PullInfo = dict[str, Any]
IssueInfo = dict[str, Any]


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


class SessionActivity:
    """Claude Code session activity data keyed by repository name."""

    def __init__(self, data: dict[str, list[SessionInfo]]) -> None:
        self._data = data

    def repos(self) -> dict[str, list[SessionInfo]]:
        """Return the underlying repo-keyed dict."""
        return self._data

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
    summary: str
    achievements: list[str]
    ongoing: list[str]
    claude_code: str
    tags: list[str]
    status: str


class ReportSummary(TypedDict):
    summary: str
    repositories: list[RepoSummary]


def require_env(name: str) -> str:
    """Get a required environment variable or exit with an error.

    Args:
        name: Environment variable name

    Returns:
        The environment variable value
    """
    value = os.environ.get(name)
    if not value:
        print(f"{name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def get_target_date_range(source: str | None = None) -> tuple[datetime, datetime]:
    """Return the target date range for activity fetching.

    Args:
        source: Invocation source. "manual" for manual execution,
            None or other values for scheduled execution

    Returns:
        A tuple of (since, until) as timezone-aware datetime objects.
        Scheduled: previous day JST 00:00 ~ today JST 00:00.
        Manual: today JST 00:00 ~ now
    """
    now_jst = datetime.now(JST)
    today_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    if source == "manual":
        return today_jst, now_jst

    yesterday_jst = today_jst - timedelta(days=1)
    return yesterday_jst, today_jst
