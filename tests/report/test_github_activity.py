"""Tests for GitHubActivity formatting."""

from report.domain import CommitInfo, IssueInfo, PullInfo
from report.github import GitHubActivity


def _commit(message, sha="abc1234"):
    return CommitInfo(
        sha=sha,
        message=message,
        author="user",
        date="2026-03-28T10:00:00+09:00",
        url=f"https://github.com/n-yU/repo/commit/{sha}",
    )


def _pull(number, title, state, *, labels=()):
    return PullInfo(
        number=number,
        title=title,
        state=state,
        author="user",
        labels=tuple(labels),
        draft=False,
        url=f"https://github.com/n-yU/repo/pull/{number}",
        created_at="2026-03-28T09:00:00+09:00",
        merged_at=None,
        closed_at=None,
        merge_commit_sha=None,
    )


def _issue(number, title, state, *, labels=()):
    return IssueInfo(
        number=number,
        title=title,
        state=state,
        author="user",
        labels=tuple(labels),
        url=f"https://github.com/n-yU/repo/issues/{number}",
        created_at="2026-03-28T09:00:00+09:00",
        closed_at=None,
        state_reason=None,
    )


class TestGitHubActivityFormat:
    def test_empty_activity(self):
        activity = GitHubActivity({})
        assert activity.format() == "# GitHub アクティビティ\nアクティビティなし"

    def test_with_commits_prs_issues(self):
        data = {
            "my-repo": {
                "commits": [_commit("Fix bug")],
                "pulls": [_pull(1, "Add feature", "merged", labels=("enhancement",))],
                "issues": [_issue(2, "Bug report", "closed")],
            },
        }
        result = GitHubActivity(data).format()
        assert "## my-repo" in result
        assert "- Fix bug" in result
        assert "- [merged] #1 Add feature (enhancement)" in result
        assert "- [closed] #2 Bug report" in result

    def test_repos_sorted_alphabetically(self):
        data = {
            "z-repo": {"commits": [_commit("z")], "pulls": [], "issues": []},
            "a-repo": {"commits": [_commit("a")], "pulls": [], "issues": []},
        }
        result = GitHubActivity(data).format()
        assert result.index("a-repo") < result.index("z-repo")

    def test_bool_and_contains(self):
        activity = GitHubActivity({"repo": {"commits": [], "pulls": [], "issues": []}})
        assert bool(activity)
        assert "repo" in activity
        assert "other" not in activity
        assert not bool(GitHubActivity({}))
