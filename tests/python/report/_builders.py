"""Builders for tests/report/."""

from datetime import datetime
from unittest.mock import MagicMock

from report.domain import activity
from report.domain.session import SessionActivity, SessionInfo
from report.shared import dates

OWNER = "n-yU"
REPO = "my-repo"
REPO_FULL_NAME = f"{OWNER}/{REPO}"


def jst(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dates.JST)


CREATED_AT = jst(2026, 3, 28, 9)
COMMITTED_AT = jst(2026, 3, 28, 10)
COMPLETED_AT = jst(2026, 3, 28, 10)
SESSION_START = jst(2026, 3, 28, 10)
SESSION_END = jst(2026, 3, 28, 11)

SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=dates.JST)
TARGET_DATE = SINCE  # A report covers the day its window starts on

# Marks a completion field left to the value that matches `state`
_DERIVED = object()


def commit(
    sha="abc1234",
    message="Fix bug",
    *,
    date=COMMITTED_AT,
    author="user",
    repo=REPO,
    url=None,
    pull_numbers=(),
):
    return activity.CommitInfo(
        sha=sha,
        message=message,
        author=author,
        date=date,
        url=url
        if url is not None
        else f"https://github.com/{OWNER}/{repo}/commit/{sha}",
        pull_numbers=tuple(pull_numbers),
    )


def pull(
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

    return activity.PullInfo(
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


def issue(
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
    linked_pulls=(),
):
    """Build an IssueInfo whose close timestamp defaults to the value `state` implies."""
    if closed_at is _DERIVED:
        closed_at = COMPLETED_AT if state == "closed" else None

    return activity.IssueInfo(
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
        linked_pulls=tuple(linked_pulls),
    )


def session_entry(
    *,
    session_id="s1",
    project=REPO,
    start=SESSION_START,
    end=SESSION_END,
    messages=("Fix bug",),
    tools=("Edit",),
    session_commits=None,
    session_pulls=None,
    session_issues=None,
) -> SessionInfo:
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


def session(repo=REPO, *, entries=None, **entry_kwargs):
    if entries is None:
        entries = [session_entry(project=repo, **entry_kwargs)]
    return SessionActivity({repo: entries})


def repo_activity(*, commits=(), pulls=(), issues=()) -> activity.Repo:
    return {
        "commits": list(commits),
        "pulls": list(pulls),
        "issues": list(issues),
    }


def github(repo=REPO, *, commits=(), pulls=(), issues=()):
    return activity.GitHubActivity(
        {repo: repo_activity(commits=commits, pulls=pulls, issues=issues)}
    )


def stub(cls, **attrs):
    """Instantiate `cls` with `__init__` bypassed and set the given attributes.

    Client classes build their API client from credentials that tests do not hold.
    """
    obj = cls.__new__(cls)
    for name, value in attrs.items():
        setattr(obj, name, value)
    return obj


def label_mock(name):
    label = MagicMock()
    # `name` is consumed by MagicMock's constructor, so it has to be assigned afterwards
    label.name = name
    return label


def number_mock(number):
    """Build the minimal stand-in for objects the API returns only to expose their number."""
    item = MagicMock()
    item.number = number
    return item


def commit_mock(
    sha="abc1234",
    *,
    message="Fix bug",
    date=CREATED_AT,
    author="user",
    repo=REPO,
):
    mock = MagicMock()
    mock.sha = sha
    mock.commit.message = message
    mock.commit.author.name = author
    mock.commit.author.date = date
    mock.html_url = f"https://github.com/{OWNER}/{repo}/commit/{sha}"
    return mock


def pull_mock(
    number=1,
    *,
    created_at=CREATED_AT,
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
    mock = MagicMock()
    mock.number = number
    mock.title = title
    mock.created_at = created_at
    mock.updated_at = updated_at if updated_at is not None else created_at
    mock.merged_at = merged_at
    mock.closed_at = closed_at if closed_at is not None else merged_at
    # A merged PR is closed on GitHub, so derive the state after the merged_at fallback
    mock.state = (
        state if state is not None else ("closed" if mock.closed_at else "open")
    )
    mock.draft = draft
    mock.html_url = f"https://github.com/{OWNER}/{repo}/pull/{number}"
    mock.user.login = "user"
    mock.labels = [label_mock(n) for n in labels]
    mock.merge_commit_sha = merge_commit_sha
    return mock


def cross_reference_mock(
    number,
    referenced_at=CREATED_AT,
    *,
    is_pull=True,
    repo_full_name=REPO_FULL_NAME,
):
    """Build a timeline event in which issue or PR `number` referenced the issue."""
    event = MagicMock()
    event.created_at = referenced_at
    event.source.type = "issue"
    event.source.issue.number = number
    event.source.issue.pull_request = MagicMock() if is_pull else None
    event.source.issue.repository.full_name = repo_full_name
    return event


def timeline_event_mock():
    """Build a timeline event without a referencing source, such as a label change."""
    event = MagicMock()
    event.source = None
    return event


def issue_mock(
    number=1,
    *,
    created_at=CREATED_AT,
    updated_at=None,
    closed_at=None,
    state=None,
    state_reason=None,
    title="Issue",
    labels=(),
    pull_request=None,
    repo=REPO,
):
    mock = MagicMock()
    mock.number = number
    mock.title = title
    mock.created_at = created_at
    mock.updated_at = updated_at if updated_at is not None else created_at
    mock.closed_at = closed_at
    mock.state = state if state is not None else ("closed" if closed_at else "open")
    mock.state_reason = state_reason
    mock.html_url = f"https://github.com/{OWNER}/{repo}/issues/{number}"
    mock.user.login = "user"
    mock.labels = [label_mock(n) for n in labels]
    mock.pull_request = pull_request
    return mock
