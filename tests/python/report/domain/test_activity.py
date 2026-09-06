"""Tests for report.domain.activity dataclasses and the repo-keyed container."""

import dataclasses
from datetime import datetime

import pytest

from report.domain import activity
from report.shared.dates import JST

from .._builders import (
    OWNER,
    REPO_FULL_NAME,
    SINCE,
    UNTIL,
    make_commit,
    make_commit_mock,
    make_issue,
    make_issue_mock,
    make_pull,
    make_pull_mock,
    make_session_entry,
)


def _session(
    *,
    start="2026-03-28T10:00:00+09:00",
    session_commits=(),
):
    return make_session_entry(
        start=start,
        end=start,
        messages=(),
        tools=(),
        session_commits=session_commits,
    )


def _commit(**overrides):
    base = {
        "sha": "abc1234deadbeef",
        "message": "Fix bug",
        "author": "user",
        "date": "2026-03-28T10:00:00+09:00",
        "url": "https://github.com/n-yU/my-repo/commit/abc1234deadbeef",
    }
    base.update(overrides)
    return activity.CommitInfo(**base)


def _pull(**overrides):
    base = {
        "number": 42,
        "title": "Add feature",
        "state": "merged",
        "author": "user",
        "labels": (),
        "draft": False,
        "url": "https://github.com/n-yU/my-repo/pull/42",
        "created_at": "2026-03-28T09:00:00+09:00",
        "merged_at": "2026-03-28T10:00:00+09:00",
        "closed_at": "2026-03-28T10:00:00+09:00",
        "merge_commit_sha": "deadbeef",
    }
    base.update(overrides)
    return activity.PullInfo(**base)


def _issue(**overrides):
    base = {
        "number": 7,
        "title": "Bug report",
        "state": "closed",
        "author": "user",
        "labels": (),
        "url": "https://github.com/n-yU/my-repo/issues/7",
        "created_at": "2026-03-28T09:00:00+09:00",
        "closed_at": "2026-03-28T11:00:00+09:00",
        "state_reason": None,
    }
    base.update(overrides)
    return activity.IssueInfo(**base)


def _make_pr_mock(
    *,
    pr_state="open",
    merged_at_dt=None,
    closed_at_dt=None,
    label_names=(),
):
    return make_pull_mock(
        42,
        title="PR title",
        state=pr_state,
        merged_at=merged_at_dt,
        closed_at=closed_at_dt,
        labels=label_names,
    )


class TestCommitInfo:
    def test_short_sha_uses_seven_chars(self):
        assert _commit(sha="abcdefghijklmnop").short_sha == "abcdefg"

    def test_label_combines_short_sha_and_message(self):
        c = _commit(sha="abc1234deadbeef", message="Fix bug")
        assert c.label() == "abc1234: Fix bug"

    @pytest.mark.parametrize(
        "date_str,expected",
        [
            ("2026-03-28T10:00:00+09:00", True),
            ("2026-03-28T00:00:00+09:00", True),
            ("2026-03-27T23:59:59+09:00", False),
            ("2026-03-29T00:00:00+09:00", False),
        ],
    )
    def test_is_in_range(self, date_str, expected):
        assert _commit(date=date_str).is_in_range(SINCE, UNTIL) is expected

    def test_from_search_commit_extracts_first_message_line(self):
        commit = make_commit_mock(
            sha="deadbeef",
            message="Subject line\n\nBody paragraph",
            author="alice",
            date=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
        )

        info = activity.CommitInfo.from_search_commit(commit, pull_numbers=[42, 43])

        assert info.sha == "deadbeef"
        assert info.message == "Subject line"
        assert info.author == "alice"
        assert info.date == "2026-03-28T10:00:00+09:00"
        assert info.url == f"https://github.com/{REPO_FULL_NAME}/commit/deadbeef"
        assert info.pull_numbers == (42, 43)

    def test_from_search_commit_defaults_pull_numbers_to_empty(self):
        info = activity.CommitInfo.from_search_commit(make_commit_mock())

        assert info.pull_numbers == ()

    def test_with_pull_numbers_returns_new_instance(self):
        original = _commit(pull_numbers=())
        updated = original.with_pull_numbers([1, 2])

        assert updated is not original
        assert updated.pull_numbers == (1, 2)
        assert original.pull_numbers == ()

    def test_is_frozen(self):
        c = _commit()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.sha = "other"  # type: ignore[misc]


class TestPullInfo:
    def test_label_renders_number_and_title(self):
        pr = _pull(number=42, title="Add feature")
        assert pr.label() == "#42: Add feature"

    def test_done_prefix_for_merged(self):
        assert _pull(state="merged").done_prefix() == "✅ "

    def test_done_prefix_for_closed(self):
        assert _pull(state="closed").done_prefix() == "⚠️ (closed) "

    @pytest.mark.parametrize(
        "created_at,merged_at,closed_at,expected",
        [
            ("2026-03-28T05:00:00+09:00", None, None, True),
            ("2026-03-27T05:00:00+09:00", "2026-03-28T05:00:00+09:00", None, True),
            ("2026-03-27T05:00:00+09:00", None, "2026-03-28T05:00:00+09:00", True),
            ("2026-03-27T05:00:00+09:00", None, None, False),
            ("2026-03-29T05:00:00+09:00", None, None, False),
        ],
    )
    def test_has_event_in_range(self, created_at, merged_at, closed_at, expected):
        pr = _pull(created_at=created_at, merged_at=merged_at, closed_at=closed_at)
        assert pr.has_event_in_range(SINCE, UNTIL) is expected

    @pytest.mark.parametrize(
        "state,merged_at,closed_at,expected",
        [
            ("open", None, None, "open"),
            (
                "merged",
                "2026-03-28T10:00:00+09:00",
                "2026-03-28T10:00:00+09:00",
                "merged",
            ),
            ("closed", None, "2026-03-28T10:00:00+09:00", "closed"),
            ("merged", "2026-03-27T10:00:00+09:00", "2026-03-27T10:00:00+09:00", None),
            ("closed", None, "2026-03-27T10:00:00+09:00", None),
            (
                "merged",
                "2026-03-29T10:00:00+09:00",
                "2026-03-29T10:00:00+09:00",
                "open",
            ),
            ("closed", None, "2026-03-29T10:00:00+09:00", "open"),
            # since is inclusive, until is exclusive
            (
                "merged",
                "2026-03-28T00:00:00+09:00",
                "2026-03-28T00:00:00+09:00",
                "merged",
            ),
            (
                "merged",
                "2026-03-29T00:00:00+09:00",
                "2026-03-29T00:00:00+09:00",
                "open",
            ),
        ],
    )
    def test_state_in_range(self, state, merged_at, closed_at, expected):
        pr = _pull(state=state, merged_at=merged_at, closed_at=closed_at)
        assert pr.state_in_range(SINCE, UNTIL) == expected

    def test_from_pull_request_classifies_merged(self):
        pr = _make_pr_mock(merged_at_dt=datetime(2026, 3, 28, 10, 0, tzinfo=JST))
        info = activity.PullInfo.from_pull_request(pr)
        assert info.state == "merged"
        assert info.merged_at == "2026-03-28T10:00:00+09:00"
        assert info.merge_commit_sha == "merge-sha"

    def test_from_pull_request_classifies_closed_unmerged(self):
        pr = _make_pr_mock(
            pr_state="closed",
            closed_at_dt=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
        )
        info = activity.PullInfo.from_pull_request(pr)
        assert info.state == "closed"
        assert info.merged_at is None
        # Unmerged: ignore the test-merge SHA returned by GitHub
        assert info.merge_commit_sha is None

    def test_from_pull_request_classifies_open(self):
        pr = _make_pr_mock(pr_state="open")
        info = activity.PullInfo.from_pull_request(pr)
        assert info.state == "open"
        assert info.merged_at is None
        assert info.closed_at is None
        assert info.merge_commit_sha is None

    def test_from_pull_request_extracts_labels_as_tuple(self):
        pr = _make_pr_mock(label_names=("bug", "ready"))
        info = activity.PullInfo.from_pull_request(pr)
        assert info.labels == ("bug", "ready")


class TestIssueInfo:
    def test_label_renders_number_and_title(self):
        issue = _issue(number=7, title="Bug report")
        assert issue.label() == "#7: Bug report"

    @pytest.mark.parametrize(
        "state_reason,expected",
        [
            (None, "✅ "),
            ("completed", "✅ "),
            ("not_planned", "⚠️ (not planned) "),
            ("duplicate", "⚠️ (duplicate) "),
        ],
    )
    def test_done_prefix(self, state_reason, expected):
        assert _issue(state_reason=state_reason).done_prefix() == expected

    @pytest.mark.parametrize(
        "state_reason,expected",
        [
            (None, "✅ close: "),
            ("completed", "✅ close: "),
            ("not_planned", "⚠️ close (not planned): "),
            ("duplicate", "⚠️ close (duplicate): "),
        ],
    )
    def test_timeline_close_prefix(self, state_reason, expected):
        assert _issue(state_reason=state_reason).timeline_close_prefix() == expected

    @pytest.mark.parametrize(
        "created_at,closed_at,expected",
        [
            ("2026-03-28T05:00:00+09:00", None, True),
            ("2026-03-27T05:00:00+09:00", "2026-03-28T05:00:00+09:00", True),
            ("2026-03-27T05:00:00+09:00", None, False),
            ("2026-03-29T05:00:00+09:00", None, False),
        ],
    )
    def test_has_event_in_range(self, created_at, closed_at, expected):
        issue = _issue(created_at=created_at, closed_at=closed_at)
        assert issue.has_event_in_range(SINCE, UNTIL) is expected

    @pytest.mark.parametrize(
        "state,closed_at,expected",
        [
            ("open", None, "open"),
            ("closed", "2026-03-28T10:00:00+09:00", "closed"),
            ("closed", "2026-03-27T10:00:00+09:00", None),
            ("closed", "2026-03-29T10:00:00+09:00", "open"),
            # since is inclusive, until is exclusive
            ("closed", "2026-03-28T00:00:00+09:00", "closed"),
            ("closed", "2026-03-29T00:00:00+09:00", "open"),
        ],
    )
    def test_state_in_range(self, state, closed_at, expected):
        issue = _issue(state=state, closed_at=closed_at)
        assert issue.state_in_range(SINCE, UNTIL) == expected

    def test_from_issue_extracts_labels_as_tuple(self):
        issue = make_issue_mock(7, labels=("bug", "priority:high"))

        info = activity.IssueInfo.from_issue(issue)

        assert info.labels == ("bug", "priority:high")
        assert info.closed_at is None
        assert info.state == "open"

    def test_from_issue_serializes_closed_at(self):
        issue = make_issue_mock(
            7,
            closed_at=datetime(2026, 3, 28, 11, 0, tzinfo=JST),
            state_reason="not_planned",
        )

        info = activity.IssueInfo.from_issue(issue)

        assert info.closed_at == "2026-03-28T11:00:00+09:00"
        assert info.state_reason == "not_planned"


class TestGitHubActivityFormat:
    def test_empty_activity(self):
        github_activity = activity.GitHubActivity({})
        assert (
            github_activity.format(SINCE, UNTIL)
            == "# GitHub アクティビティ\nアクティビティなし"
        )

    def test_with_commits_prs_issues(self):
        data = {
            "my-repo": {
                "commits": [make_commit(message="Fix bug")],
                "pulls": [
                    make_pull(
                        1,
                        "Add feature",
                        "merged",
                        labels=("enhancement",),
                        merged_at="2026-03-28T10:00:00+09:00",
                    )
                ],
                "issues": [
                    make_issue(
                        2, "Bug report", "closed", closed_at="2026-03-28T11:00:00+09:00"
                    )
                ],
            },
        }
        result = activity.GitHubActivity(data).format(SINCE, UNTIL)
        assert "## my-repo" in result
        assert "- Fix bug" in result
        assert "- [merged] #1 Add feature (enhancement)" in result
        assert "- [closed] #2 Bug report" in result

    def test_repos_sorted_alphabetically(self):
        data = {
            "z-repo": {
                "commits": [make_commit(message="z")],
                "pulls": [],
                "issues": [],
            },
            "a-repo": {
                "commits": [make_commit(message="a")],
                "pulls": [],
                "issues": [],
            },
        }
        result = activity.GitHubActivity(data).format(SINCE, UNTIL)
        assert result.index("a-repo") < result.index("z-repo")

    def test_omits_items_completed_before_window(self):
        data = {
            "my-repo": {
                "commits": [
                    make_commit(message="Yesterday", date="2026-03-27T10:00:00+09:00")
                ],
                "pulls": [
                    make_pull(
                        1, "Merged", "merged", merged_at="2026-03-27T10:00:00+09:00"
                    )
                ],
                "issues": [
                    make_issue(
                        2, "Closed", "closed", closed_at="2026-03-27T11:00:00+09:00"
                    )
                ],
            },
        }
        result = activity.GitHubActivity(data).format(SINCE, UNTIL)
        assert result == "# GitHub アクティビティ\nアクティビティなし"

    def test_reports_items_completed_after_window_as_open(self):
        data = {
            "my-repo": {
                "commits": [],
                "pulls": [
                    make_pull(
                        1, "Merged", "merged", merged_at="2026-03-29T10:00:00+09:00"
                    )
                ],
                "issues": [
                    make_issue(
                        2, "Closed", "closed", closed_at="2026-03-29T11:00:00+09:00"
                    )
                ],
            },
        }
        result = activity.GitHubActivity(data).format(SINCE, UNTIL)
        assert "- [open] #1 Merged" in result
        assert "- [open] #2 Closed" in result

    def test_bool_and_contains(self):
        github_activity = activity.GitHubActivity(
            {"repo": {"commits": [], "pulls": [], "issues": []}}
        )
        assert bool(github_activity)
        assert "repo" in github_activity
        assert "other" not in github_activity
        assert not bool(activity.GitHubActivity({}))


class TestMergeSessionCommits:
    def _populate_no_op(self, repo_name, commits):
        pass

    def test_does_nothing_when_no_session_commits(self):
        github_activity = activity.GitHubActivity({})
        github_activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[])],
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        assert github_activity.repos() == {}

    def test_adds_new_repo_when_missing(self):
        github_activity = activity.GitHubActivity({})
        calls: list[tuple[str, list[activity.CommitInfo]]] = []

        def populate(repo_name, commits):
            calls.append((repo_name, list(commits)))

        github_activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "abc1234", "message": "Fix login"}])],
            owner=OWNER,
            populate_pull_numbers=populate,
        )

        repo = github_activity.repos()["my-repo"]
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
        existing = activity.CommitInfo(
            sha="aaa1111",
            message="Existing",
            author="user",
            date="2026-03-28T09:00:00+09:00",
            url=f"https://github.com/{OWNER}/my-repo/commit/aaa1111",
        )
        github_activity = activity.GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        github_activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "bbb2222", "message": "New"}])],
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = github_activity.repos()["my-repo"]["commits"]
        assert [c.sha for c in commits] == ["aaa1111", "bbb2222"]

    def test_drops_session_commit_already_covered_by_search(self):
        # GitHub search returns the full 40-char SHA; the session captures only the short prefix
        full_sha = "abc1234abcdef1234abcdef1234abcdef12345678"
        existing = activity.CommitInfo(
            sha=full_sha,
            message="Existing",
            author="user",
            date="2026-03-28T09:00:00+09:00",
            url=f"https://github.com/{OWNER}/my-repo/commit/{full_sha}",
        )
        github_activity = activity.GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        populate_calls: list[tuple[str, list[activity.CommitInfo]]] = []

        def populate(repo_name, commits):
            populate_calls.append((repo_name, list(commits)))

        github_activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "abc1234", "message": "Same commit"}])],
            owner=OWNER,
            populate_pull_numbers=populate,
        )
        commits = github_activity.repos()["my-repo"]["commits"]
        assert [c.sha for c in commits] == [full_sha]
        # populate must not be invoked when nothing is actually injected
        assert populate_calls == []

    def test_deduplicates_session_commits_across_sessions(self):
        github_activity = activity.GitHubActivity({})
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
        github_activity.merge_session_commits(
            "my-repo",
            sessions,
            owner=OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = github_activity.repos()["my-repo"]["commits"]
        assert len(commits) == 1
        # First-occurrence wins; the second session's duplicate is dropped entirely
        assert commits[0].message == "First"
        assert commits[0].date == "2026-03-28T10:30:00+09:00"

    def test_uses_per_commit_timestamp_when_present(self):
        github_activity = activity.GitHubActivity({})
        github_activity.merge_session_commits(
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
        commits = github_activity.repos()["my-repo"]["commits"]
        # Per-commit timestamp wins; legacy entry falls back to the session start_time
        by_sha = {c.sha: c for c in commits}
        assert by_sha["aaa"].date == "2026-03-28T10:45:00+09:00"
        assert by_sha["bbb"].date == "2026-03-28T10:00:00+09:00"
