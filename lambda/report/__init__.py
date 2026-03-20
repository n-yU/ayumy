"""Ayumy daily report generator.

Fetches GitHub activity and Claude Code session logs for the target
date range and formats them for downstream processing.
"""

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


Activity = dict[str, RepoActivity]


class SessionInfo(TypedDict):
    session_id: str
    project: str
    start_time: str
    end_time: str
    user_messages: list[str]
    tools_used: list[str]


SessionActivity = dict[str, list[SessionInfo]]


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
