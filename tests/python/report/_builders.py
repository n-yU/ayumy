"""Builders and assert helpers for tests/report/."""

from datetime import datetime
from unittest.mock import MagicMock

from report.domain.activity import CommitInfo, GitHubActivity, IssueInfo, PullInfo
from report.domain.session import SessionActivity
from report.shared.dates import JST

OWNER = "n-yU"
REPO = "my-repo"
REPO_FULL_NAME = f"{OWNER}/{REPO}"
MOCK_CREATED_AT = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
CREATED_AT = MOCK_CREATED_AT.isoformat()
COMPLETED_AT = datetime(2026, 3, 28, 10, 0, tzinfo=JST).isoformat()

SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
TARGET_DATE = SINCE  # A report covers the day its window starts on

# Marks a completion field left to the value that matches `state`
_DERIVED = object()


def make_commit(
    sha="abc1234",
    message="Fix bug",
    *,
    date="2026-03-28T10:00:00+09:00",
    author="user",
    repo=REPO,
    url=None,
    pull_numbers=(),
):
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
    repo=REPO,
    created_at=CREATED_AT,
    merged_at=_DERIVED,
    closed_at=_DERIVED,
    merge_commit_sha=_DERIVED,
):
    """Build a PullInfo whose completion fields default to the values `state` implies."""
    if merged_at is _DERIVED:
        merged_at = COMPLETED_AT if state == "merged" else None
    if closed_at is _DERIVED:
        # GitHub closes a PR at the moment it merges
        closed_at = merged_at or (COMPLETED_AT if state == "closed" else None)
    if merge_commit_sha is _DERIVED:
        merge_commit_sha = "deadbeef" if state == "merged" else None

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
    repo=REPO,
    created_at=CREATED_AT,
    closed_at=_DERIVED,
    state_reason=None,
):
    """Build an IssueInfo whose close timestamp defaults to the value `state` implies."""
    if closed_at is _DERIVED:
        closed_at = COMPLETED_AT if state == "closed" else None

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
    project=REPO,
    start="2026-03-28T10:00:00+09:00",
    end="2026-03-28T11:00:00+09:00",
    messages=("Fix bug",),
    tools=("Edit",),
    session_commits=None,
    session_pulls=None,
    session_issues=None,
):
    return {
        "session_id": session_id,
        "project": project,
        "start_time": start,
        "end_time": end,
        "user_messages": list(messages),
        "tools_used": list(tools),
        "session_commits": list(session_commits or ()),
        "session_pulls": list(session_pulls or ()),
        "session_issues": list(session_issues or ()),
    }


def make_session(repo=REPO, *, entries=None, **entry_kwargs):
    if entries is None:
        entries = [make_session_entry(project=repo, **entry_kwargs)]
    return SessionActivity({repo: entries})


def make_repo_activity(*, commits=(), pulls=(), issues=()):
    return {
        "commits": list(commits),
        "pulls": list(pulls),
        "issues": list(issues),
    }


def make_github(repo=REPO, *, commits=(), pulls=(), issues=()):
    return GitHubActivity(
        {repo: make_repo_activity(commits=commits, pulls=pulls, issues=issues)}
    )


def make_stub(cls, **attrs):
    """Instantiate `cls` with `__init__` bypassed and set the given attributes.

    Client classes build their API client from credentials that tests do not hold.
    """
    obj = cls.__new__(cls)
    for name, value in attrs.items():
        setattr(obj, name, value)
    return obj


def make_label_mock(name):
    label = MagicMock()
    # `name` is consumed by MagicMock's constructor, so it has to be assigned afterwards
    label.name = name
    return label


def make_number_mock(number):
    """Build the minimal stand-in for objects the API returns only to expose their number."""
    item = MagicMock()
    item.number = number
    return item


def make_commit_mock(
    sha="abc1234",
    *,
    message="Fix bug",
    date=MOCK_CREATED_AT,
    author="user",
    repo=REPO,
):
    commit = MagicMock()
    commit.sha = sha
    commit.commit.message = message
    commit.commit.author.name = author
    commit.commit.author.date = date
    commit.html_url = f"https://github.com/{OWNER}/{repo}/commit/{sha}"
    return commit


def make_pull_mock(
    number=1,
    *,
    created_at=MOCK_CREATED_AT,
    updated_at=None,
    merged_at=None,
    closed_at=None,
    state=None,
    title="PR",
    labels=(),
    draft=False,
    repo=REPO,
    merge_commit_sha="merge-sha",
):
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.created_at = created_at
    pr.updated_at = updated_at if updated_at is not None else created_at
    pr.merged_at = merged_at
    pr.closed_at = closed_at if closed_at is not None else merged_at
    # A merged PR is closed on GitHub, so derive the state after the merged_at fallback
    pr.state = state if state is not None else ("closed" if pr.closed_at else "open")
    pr.draft = draft
    pr.html_url = f"https://github.com/{OWNER}/{repo}/pull/{number}"
    pr.user.login = "user"
    pr.labels = [make_label_mock(n) for n in labels]
    pr.merge_commit_sha = merge_commit_sha
    return pr


def make_issue_mock(
    number=1,
    *,
    created_at=MOCK_CREATED_AT,
    updated_at=None,
    closed_at=None,
    state=None,
    state_reason=None,
    title="Issue",
    labels=(),
    pull_request=None,
    repo=REPO,
):
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.created_at = created_at
    issue.updated_at = updated_at if updated_at is not None else created_at
    issue.closed_at = closed_at
    issue.state = state if state is not None else ("closed" if closed_at else "open")
    issue.state_reason = state_reason
    issue.html_url = f"https://github.com/{OWNER}/{repo}/issues/{number}"
    issue.user.login = "user"
    issue.labels = [make_label_mock(n) for n in labels]
    issue.pull_request = pull_request
    return issue


def assert_published(clients):
    """Verify report → Notion → Slack ran once."""
    clients["summary_client"].generate_summary.assert_called_once()
    clients["notion_client"].create_report_pages.assert_called_once()
    clients["slack_client"].notify.assert_called_once()
    clients["slack_client"].notify_validation_errors.assert_not_called()


def assert_skipped(clients, since):
    clients["summary_client"].generate_summary.assert_not_called()
    clients["notion_client"].create_report_pages.assert_not_called()
    clients["slack_client"].notify.assert_not_called()
    clients["slack_client"].notify_validation_errors.assert_not_called()
    clients["slack_client"].notify_no_activity.assert_called_once_with(
        since, is_backfill=False
    )
