"""Tests for GitHubActivity formatting and session-commit merging."""

from report.domain import CommitInfo, IssueInfo, PullInfo
from report.github import GitHubActivity

OWNER = "n-yU"


def _session(
    *,
    start="2026-03-28T10:00:00+09:00",
    session_commits=(),
):
    """Build a SessionInfo-shaped dict carrying only the fields the merge reads."""
    return {
        "session_id": "s1",
        "project": "my-repo",
        "start_time": start,
        "end_time": start,
        "user_messages": [],
        "tools_used": [],
        "session_commits": list(session_commits),
        "session_pulls": [],
        "session_issues": [],
    }


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


class TestMergeSessionCommits:
    def _populate_no_op(self, repo_name, commits):
        pass

    def test_does_nothing_when_no_session_commits(self):
        activity = GitHubActivity({})
        activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[])],
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        assert activity.repos() == {}

    def test_adds_new_repo_when_missing(self):
        activity = GitHubActivity({})
        calls: list[tuple[str, list[CommitInfo]]] = []

        def populate(repo_name, commits):
            calls.append((repo_name, list(commits)))

        activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "abc1234", "message": "Fix login"}])],
            owner=OWNER,
            populate_pull_numbers=populate,
        )

        repo = activity.repos()["my-repo"]
        assert len(repo["commits"]) == 1
        assert repo["commits"][0].sha == "abc1234"
        assert repo["commits"][0].message == "Fix login"
        assert (
            repo["commits"][0].url
            == f"https://github.com/{OWNER}/my-repo/commit/abc1234"
        )
        assert repo["commits"][0].author == ""
        assert repo["pulls"] == []
        assert repo["issues"] == []
        assert len(calls) == 1 and calls[0][0] == "my-repo"

    def test_extends_existing_repo_with_new_commits(self):
        existing = CommitInfo(
            sha="aaa1111",
            message="Existing",
            author="user",
            date="2026-03-28T09:00:00+09:00",
            url=f"https://github.com/{OWNER}/my-repo/commit/aaa1111",
        )
        activity = GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "bbb2222", "message": "New"}])],
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = activity.repos()["my-repo"]["commits"]
        assert [c.sha for c in commits] == ["aaa1111", "bbb2222"]

    def test_drops_session_commit_already_covered_by_search(self):
        # GitHub search returns the full 40-char SHA; the session captures only the short prefix
        full_sha = "abc1234abcdef1234abcdef1234abcdef12345678"
        existing = CommitInfo(
            sha=full_sha,
            message="Existing",
            author="user",
            date="2026-03-28T09:00:00+09:00",
            url=f"https://github.com/{OWNER}/my-repo/commit/{full_sha}",
        )
        activity = GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        populate_calls: list[tuple[str, list[CommitInfo]]] = []

        def populate(repo_name, commits):
            populate_calls.append((repo_name, list(commits)))

        activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "abc1234", "message": "Same commit"}])],
            owner=OWNER,
            populate_pull_numbers=populate,
        )
        commits = activity.repos()["my-repo"]["commits"]
        assert [c.sha for c in commits] == [full_sha]
        # populate must not be invoked when nothing is actually injected
        assert populate_calls == []

    def test_deduplicates_session_commits_across_sessions(self):
        activity = GitHubActivity({})
        sessions = [
            _session(
                start="2026-03-28T10:00:00+09:00",
                session_commits=[
                    {
                        "sha": "aaa1111",
                        "message": "First",
                        "timestamp": "2026-03-28T10:30:00+09:00",
                    },
                ],
            ),
            _session(
                start="2026-03-28T12:00:00+09:00",
                session_commits=[
                    {
                        "sha": "aaa1111",
                        "message": "Duplicate",
                        "timestamp": "2026-03-28T12:30:00+09:00",
                    },
                ],
            ),
        ]
        activity.merge_session_commits(
            "my-repo",
            sessions,
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = activity.repos()["my-repo"]["commits"]
        assert len(commits) == 1
        # First-occurrence wins; the second session's duplicate is dropped entirely
        assert commits[0].message == "First"
        assert commits[0].date == "2026-03-28T10:30:00+09:00"

    def test_uses_per_commit_timestamp_when_present(self):
        activity = GitHubActivity({})
        activity.merge_session_commits(
            "my-repo",
            [
                _session(
                    start="2026-03-28T10:00:00+09:00",
                    session_commits=[
                        {
                            "sha": "aaa",
                            "message": "C1",
                            "timestamp": "2026-03-28T10:45:00+09:00",
                        },
                        {"sha": "bbb", "message": "C2"},
                    ],
                )
            ],
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = activity.repos()["my-repo"]["commits"]
        # Per-commit timestamp wins; legacy entry falls back to the session start_time
        by_sha = {c.sha: c for c in commits}
        assert by_sha["aaa"].date == "2026-03-28T10:45:00+09:00"
        assert by_sha["bbb"].date == "2026-03-28T10:00:00+09:00"

    def test_skips_populate_when_existing_repo_has_no_new_commits(self):
        existing = CommitInfo(
            sha="aaa1111abcdef",
            message="Existing",
            author="user",
            date="2026-03-28T09:00:00+09:00",
            url=f"https://github.com/{OWNER}/my-repo/commit/aaa1111abcdef",
        )
        activity = GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        called = []

        def populate(repo_name, commits):
            called.append(repo_name)

        activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "aaa1111", "message": "covered"}])],
            owner=OWNER,
            populate_pull_numbers=populate,
        )
        assert called == []
