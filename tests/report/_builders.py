"""Builders and assert helpers for tests/report/."""

from report import SessionActivity
from report.domain import CommitInfo, IssueInfo, PullInfo
from report.github import GitHubActivity

OWNER = "n-yU"


def make_commit(
    sha="abc1234",
    message="Fix bug",
    *,
    date="2026-03-28T10:00:00",
    author="user",
    repo="my-repo",
    url=None,
    pull_numbers=(),
):
    """Build a CommitInfo dataclass for use in tests."""
    return CommitInfo(
        sha=sha,
        message=message,
        author=author,
        date=date,
        url=url
        if url is not None
        else f"https://github.com/{OWNER}/{repo}/commit/{sha}",
        pull_numbers=tuple(pull_numbers),
    )


def make_pull(
    number=1,
    title="PR title",
    state="merged",
    *,
    author="user",
    labels=(),
    draft=False,
    url=None,
    repo="my-repo",
    created_at="2026-03-28T09:00:00+09:00",
    merged_at="2026-03-28T10:00:00+09:00",
    closed_at="2026-03-28T10:00:00+09:00",
    merge_commit_sha="deadbeef",
):
    """Build a PullInfo dataclass for use in tests."""
    return PullInfo(
        number=number,
        title=title,
        state=state,
        author=author,
        labels=tuple(labels),
        draft=draft,
        url=url
        if url is not None
        else f"https://github.com/{OWNER}/{repo}/pull/{number}",
        created_at=created_at,
        merged_at=merged_at,
        closed_at=closed_at,
        merge_commit_sha=merge_commit_sha,
    )


def make_issue(
    number=1,
    title="Issue title",
    state="open",
    *,
    author="user",
    labels=(),
    url=None,
    repo="my-repo",
    created_at="2026-03-28T09:00:00+09:00",
    closed_at=None,
    state_reason=None,
):
    """Build an IssueInfo dataclass for use in tests."""
    return IssueInfo(
        number=number,
        title=title,
        state=state,
        author=author,
        labels=tuple(labels),
        url=url
        if url is not None
        else f"https://github.com/{OWNER}/{repo}/issues/{number}",
        created_at=created_at,
        closed_at=closed_at,
        state_reason=state_reason,
    )


def make_session_entry(
    *,
    session_id="s1",
    project="my-repo",
    start="2026-03-28T10:00:00+09:00",
    end="2026-03-28T11:00:00+09:00",
    messages=("Fix bug",),
    tools=("Edit",),
    session_commits=None,
    session_pulls=None,
    session_issues=None,
):
    """Build a single session entry dict. Optional `session_*` keys are omitted when None."""
    entry = {
        "session_id": session_id,
        "project": project,
        "start_time": start,
        "end_time": end,
        "user_messages": list(messages),
        "tools_used": list(tools),
    }
    if session_commits is not None:
        entry["session_commits"] = session_commits
    if session_pulls is not None:
        entry["session_pulls"] = session_pulls
    if session_issues is not None:
        entry["session_issues"] = session_issues
    return entry


def make_session(repo="my-repo", *, entries=None, **entry_kwargs):
    """Build a SessionActivity for one repo. Use `entries=[...]` for multi-entry; otherwise kwargs are forwarded to make_session_entry."""
    if entries is None:
        entries = [make_session_entry(project=repo, **entry_kwargs)]
    return SessionActivity({repo: entries})


def make_github(repo="my-repo", *, commits=(), pulls=(), issues=()):
    """Build a GitHubActivity for one repo."""
    return GitHubActivity(
        {
            repo: {
                "commits": list(commits),
                "pulls": list(pulls),
                "issues": list(issues),
            }
        }
    )


def assert_published(clients):
    """Assert the publish path (report → Notion → Slack) was fully exercised once."""
    clients["summary_client"].generate_summary.assert_called_once()
    clients["notion_client"].create_report_pages.assert_called_once()
    clients["slack_client"].notify.assert_called_once()
    clients["slack_client"].notify_validation_errors.assert_not_called()


def assert_skipped(clients, since):
    """Assert the no-activity path skipped all generation and only notified Slack."""
    clients["summary_client"].generate_summary.assert_not_called()
    clients["notion_client"].create_report_pages.assert_not_called()
    clients["slack_client"].notify.assert_not_called()
    clients["slack_client"].notify_validation_errors.assert_not_called()
    clients["slack_client"].notify_no_activity.assert_called_once_with(since)
