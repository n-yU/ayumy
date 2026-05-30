"""Ayumy daily report generator."""

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
    """Commit recovered from session logs; `timestamp` may be absent on legacy DynamoDB entries."""

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
    merge_commit_sha: str | None


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
        return self._data

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def format(self) -> str:
        """Format GitHub activity as the Markdown block consumed by the Claude API prompt (Spec.md §5.4)."""
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
                    lines.append(
                        f"- [{pr['state']}] #{pr['number']} {pr['title']}{labels}"
                    )

            if data["issues"]:
                lines.append("### Issues")
                for issue in data["issues"]:
                    labels = (
                        f" ({', '.join(issue['labels'])})" if issue["labels"] else ""
                    )
                    lines.append(
                        f"- [{issue['state']}] #{issue['number']} {issue['title']}{labels}"
                    )

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
        return self._data

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(
        self, key: str, default: list[SessionInfo] | None = None
    ) -> list[SessionInfo] | None:
        return self._data.get(key, default)

    def format(self) -> str:
        """Format session logs as the Markdown block consumed by the Claude API prompt."""
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
        """Return JST HH:MM from an ISO 8601 timestamp; falls back to "??:??" on empty input."""
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
    """Return the environment variable value.

    Raises:
        ValueError: If the variable is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value


@lru_cache
def get_version() -> str:
    """Return the ayumy version recorded in the VERSION file."""
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    return version_path.read_text().strip()


def date_to_range(target: date) -> tuple[datetime, datetime]:
    """Return the JST [00:00, next-day 00:00) window for the target date."""
    since = datetime(target.year, target.month, target.day, tzinfo=JST)
    until = since + timedelta(days=1)
    return since, until


def parse_target_dates(target_date: str) -> list[date]:
    """Parse a single `YYYY-MM-DD` or a `YYYY-MM-DD..YYYY-MM-DD` range into an inclusive date list.

    The range is capped at 31 days to bound activity fetch volume.

    Raises:
        ValueError: If the start date is after the end date, or the range exceeds 31 days.
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
    """Return the (since, until) window for activity fetching.

    `target_date` takes precedence and uses the first date's JST [00:00, next-day 00:00) window.
    Otherwise `source="manual"` returns today 00:00 ~ now, and any other value returns the prior day's JST window.
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
