"""Builders and assert helpers for tests/report/."""

from report import GitHubActivity, SessionActivity

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
    """Build a GitHub-style commit dict matching CommitInfo."""
    return {
        "message": message,
        "sha": sha,
        "author": author,
        "date": date,
        "url": url
        if url is not None
        else f"https://github.com/{OWNER}/{repo}/commit/{sha}",
        "pull_numbers": list(pull_numbers),
    }


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
