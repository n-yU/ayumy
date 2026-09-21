"""Tests for report.domain.activity dataclasses and the repo-keyed container."""

import dataclasses

import pytest

from report.domain import activity

from .. import _builders


def _session(
    *,
    start=_builders.SESSION_START,
    session_commits=(),
):
    return _builders.session_entry(
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
        "date": _builders.jst(2026, 3, 28, 10),
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
        "created_at": _builders.jst(2026, 3, 28, 9),
        "merged_at": _builders.jst(2026, 3, 28, 10),
        "closed_at": _builders.jst(2026, 3, 28, 10),
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
        "created_at": _builders.jst(2026, 3, 28, 9),
        "closed_at": _builders.jst(2026, 3, 28, 11),
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
    return _builders.pull_mock(
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
        "date,expected",
        [
            (_builders.jst(2026, 3, 28, 10), True),
            (_builders.jst(2026, 3, 28), True),
            (_builders.jst(2026, 3, 27, 23, 59, 59), False),
            (_builders.jst(2026, 3, 29), False),
        ],
    )
    def test_is_in_range(self, date, expected):
        assert (
            _commit(date=date).is_in_range(_builders.SINCE, _builders.UNTIL) is expected
        )

    def test_from_commit_extracts_first_message_line(self):
        commit = _builders.commit_mock(
            sha="deadbeef",
            message="Subject line\n\nBody paragraph",
            author="alice",
            date=_builders.jst(2026, 3, 28, 10),
        )

        info = activity.CommitInfo.from_commit(commit, pull_numbers=[42, 43])

        assert info.sha == "deadbeef"
        assert info.message == "Subject line"
        assert info.author == "alice"
        assert info.date == _builders.jst(2026, 3, 28, 10)
        assert (
            info.url == f"https://github.com/{_builders.REPO_FULL_NAME}/commit/deadbeef"
        )
        assert info.pull_numbers == (42, 43)

    def test_from_commit_defaults_pull_numbers_to_empty(self):
        info = activity.CommitInfo.from_commit(_builders.commit_mock())

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
            c.sha = "other"


class TestPullInfo:
    def test_label_renders_number_and_title(self):
        pr = _pull(number=42, title="Add feature")
        assert pr.label() == "#42: Add feature"

    def test_pending_prefix_for_open(self):
        assert _pull(state="open").pending_prefix() == "🟢 "

    def test_done_prefix_for_merged(self):
        assert _pull(state="merged").done_prefix() == "🟣 "

    def test_done_prefix_for_closed(self):
        assert _pull(state="closed").done_prefix() == "🔴 "

    @pytest.mark.parametrize(
        "created_at,merged_at,closed_at,expected",
        [
            (_builders.jst(2026, 3, 28, 5), None, None, True),
            (_builders.jst(2026, 3, 27, 5), _builders.jst(2026, 3, 28, 5), None, True),
            (_builders.jst(2026, 3, 27, 5), None, _builders.jst(2026, 3, 28, 5), True),
            (_builders.jst(2026, 3, 27, 5), None, None, False),
            (_builders.jst(2026, 3, 29, 5), None, None, False),
        ],
    )
    def test_has_event_in_range(self, created_at, merged_at, closed_at, expected):
        pr = _pull(created_at=created_at, merged_at=merged_at, closed_at=closed_at)
        assert pr.has_event_in_range(_builders.SINCE, _builders.UNTIL) is expected

    @pytest.mark.parametrize(
        "state,merged_at,closed_at,expected",
        [
            ("open", None, None, "open"),
            (
                "merged",
                _builders.jst(2026, 3, 28, 10),
                _builders.jst(2026, 3, 28, 10),
                "merged",
            ),
            ("closed", None, _builders.jst(2026, 3, 28, 10), "closed"),
            (
                "merged",
                _builders.jst(2026, 3, 27, 10),
                _builders.jst(2026, 3, 27, 10),
                None,
            ),
            ("closed", None, _builders.jst(2026, 3, 27, 10), None),
            (
                "merged",
                _builders.jst(2026, 3, 29, 10),
                _builders.jst(2026, 3, 29, 10),
                "open",
            ),
            ("closed", None, _builders.jst(2026, 3, 29, 10), "open"),
            # since is inclusive, until is exclusive
            (
                "merged",
                _builders.jst(2026, 3, 28),
                _builders.jst(2026, 3, 28),
                "merged",
            ),
            ("merged", _builders.jst(2026, 3, 29), _builders.jst(2026, 3, 29), "open"),
        ],
    )
    def test_state_in_range(self, state, merged_at, closed_at, expected):
        pr = _pull(state=state, merged_at=merged_at, closed_at=closed_at)
        assert pr.state_in_range(_builders.SINCE, _builders.UNTIL) == expected

    def test_from_pull_request_classifies_merged(self):
        pr = _make_pr_mock(merged_at_dt=_builders.jst(2026, 3, 28, 10))
        info = activity.PullInfo.from_pull_request(pr)
        assert info.state == "merged"
        assert info.merged_at == _builders.jst(2026, 3, 28, 10)
        assert info.merge_commit_sha == "merge-sha"

    def test_from_pull_request_classifies_closed_unmerged(self):
        pr = _make_pr_mock(
            pr_state="closed",
            closed_at_dt=_builders.jst(2026, 3, 28, 10),
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

    def test_pending_prefix_for_open(self):
        assert _issue(state="open").pending_prefix() == "🟩 "

    @pytest.mark.parametrize(
        "state_reason,expected",
        [
            (None, "🟪 "),
            ("completed", "🟪 "),
            ("not_planned", "⬜ (not planned) "),
            ("duplicate", "⬜ (duplicate) "),
        ],
    )
    def test_done_prefix(self, state_reason, expected):
        assert _issue(state_reason=state_reason).done_prefix() == expected

    @pytest.mark.parametrize(
        "state_reason,expected",
        [
            (None, "🟪 close: "),
            ("completed", "🟪 close: "),
            ("not_planned", "⬜ close (not planned): "),
            ("duplicate", "⬜ close (duplicate): "),
        ],
    )
    def test_timeline_close_prefix(self, state_reason, expected):
        assert _issue(state_reason=state_reason).timeline_close_prefix() == expected

    @pytest.mark.parametrize(
        "created_at,closed_at,expected",
        [
            (_builders.jst(2026, 3, 28, 5), None, True),
            (_builders.jst(2026, 3, 27, 5), _builders.jst(2026, 3, 28, 5), True),
            (_builders.jst(2026, 3, 27, 5), None, False),
            (_builders.jst(2026, 3, 29, 5), None, False),
        ],
    )
    def test_has_event_in_range(self, created_at, closed_at, expected):
        issue = _issue(created_at=created_at, closed_at=closed_at)
        assert issue.has_event_in_range(_builders.SINCE, _builders.UNTIL) is expected

    @pytest.mark.parametrize(
        "state,closed_at,expected",
        [
            ("open", None, "open"),
            ("closed", _builders.jst(2026, 3, 28, 10), "closed"),
            ("closed", _builders.jst(2026, 3, 27, 10), None),
            ("closed", _builders.jst(2026, 3, 29, 10), "open"),
            # since is inclusive, until is exclusive
            ("closed", _builders.jst(2026, 3, 28), "closed"),
            ("closed", _builders.jst(2026, 3, 29), "open"),
        ],
    )
    def test_state_in_range(self, state, closed_at, expected):
        issue = _issue(state=state, closed_at=closed_at)
        assert issue.state_in_range(_builders.SINCE, _builders.UNTIL) == expected

    @pytest.mark.parametrize(
        "referenced_at,expected",
        [
            (_builders.jst(2026, 3, 27, 10), True),
            (_builders.jst(2026, 3, 28, 10), True),
            # until is exclusive, so a reference made at or after it is ignored
            (_builders.jst(2026, 3, 29), False),
            (_builders.jst(2026, 3, 30, 10), False),
        ],
    )
    def test_has_linked_pull(self, referenced_at, expected):
        issue = _issue(
            state="open",
            closed_at=None,
            linked_pulls=(activity.LinkedPull(42, referenced_at),),
        )
        assert issue.has_linked_pull(_builders.UNTIL) is expected

    def test_has_linked_pull_without_links(self):
        assert _issue().has_linked_pull(_builders.UNTIL) is False

    def test_with_linked_pulls_returns_new_instance(self):
        issue = _issue()
        linked = activity.LinkedPull(42, _builders.jst(2026, 3, 28, 10))

        updated = issue.with_linked_pulls([linked])

        assert updated.linked_pulls == (linked,)
        assert issue.linked_pulls == ()

    def test_from_issue_extracts_labels_as_tuple(self):
        issue = _builders.issue_mock(7, labels=("bug", "priority:high"))

        info = activity.IssueInfo.from_issue(issue)

        assert info.labels == ("bug", "priority:high")
        assert info.closed_at is None
        assert info.state == "open"

    def test_from_issue_keeps_closed_at(self):
        issue = _builders.issue_mock(
            7,
            closed_at=_builders.jst(2026, 3, 28, 11),
            state_reason="not_planned",
        )

        info = activity.IssueInfo.from_issue(issue)

        assert info.closed_at == _builders.jst(2026, 3, 28, 11)
        assert info.state_reason == "not_planned"


class TestGitHubActivityFormat:
    def test_empty_activity(self):
        github_activity = activity.GitHubActivity({})
        assert (
            github_activity.format(_builders.SINCE, _builders.UNTIL)
            == "# GitHub アクティビティ\nアクティビティなし"
        )

    def test_with_commits_prs_issues(self):
        data = {
            "my-repo": _builders.repo_activity(
                commits=[_builders.commit(message="Fix bug")],
                pulls=[
                    _builders.pull(
                        1,
                        "Add feature",
                        "merged",
                        labels=("enhancement",),
                        merged_at=_builders.jst(2026, 3, 28, 10),
                    )
                ],
                issues=[
                    _builders.issue(
                        2,
                        "Bug report",
                        "closed",
                        closed_at=_builders.jst(2026, 3, 28, 11),
                    )
                ],
            ),
        }
        result = activity.GitHubActivity(data).format(_builders.SINCE, _builders.UNTIL)
        assert "## my-repo" in result
        assert "- Fix bug" in result
        assert "- [merged] #1 Add feature (enhancement)" in result
        assert "- [closed] #2 Bug report" in result

    def test_repos_sorted_alphabetically(self):
        data = {
            "z-repo": _builders.repo_activity(commits=[_builders.commit(message="z")]),
            "a-repo": _builders.repo_activity(commits=[_builders.commit(message="a")]),
        }
        result = activity.GitHubActivity(data).format(_builders.SINCE, _builders.UNTIL)
        assert result.index("a-repo") < result.index("z-repo")

    def test_omits_items_completed_before_window(self):
        data = {
            "my-repo": _builders.repo_activity(
                commits=[
                    _builders.commit(
                        message="Yesterday", date=_builders.jst(2026, 3, 27, 10)
                    )
                ],
                pulls=[
                    _builders.pull(
                        1, "Merged", "merged", merged_at=_builders.jst(2026, 3, 27, 10)
                    )
                ],
                issues=[
                    _builders.issue(
                        2, "Closed", "closed", closed_at=_builders.jst(2026, 3, 27, 11)
                    )
                ],
            ),
        }
        result = activity.GitHubActivity(data).format(_builders.SINCE, _builders.UNTIL)
        assert result == "# GitHub アクティビティ\nアクティビティなし"

    def test_reports_items_completed_after_window_as_open(self):
        data = {
            "my-repo": _builders.repo_activity(
                pulls=[
                    _builders.pull(
                        1, "Merged", "merged", merged_at=_builders.jst(2026, 3, 29, 10)
                    )
                ],
                issues=[
                    _builders.issue(
                        2, "Closed", "closed", closed_at=_builders.jst(2026, 3, 29, 11)
                    )
                ],
            ),
        }
        result = activity.GitHubActivity(data).format(_builders.SINCE, _builders.UNTIL)
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
            owner=_builders.OWNER,
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
            owner=_builders.OWNER,
            populate_pull_numbers=populate,
        )

        repo = github_activity.repos()["my-repo"]
        assert len(repo["commits"]) == 1
        assert repo["commits"][0].sha == "abc1234"
        assert repo["commits"][0].message == "Fix login"
        assert (
            repo["commits"][0].url
            == f"https://github.com/{_builders.OWNER}/my-repo/commit/abc1234"
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
            date=_builders.jst(2026, 3, 28, 9),
            url=f"https://github.com/{_builders.OWNER}/my-repo/commit/aaa1111",
        )
        github_activity = activity.GitHubActivity(
            {"my-repo": {"commits": [existing], "pulls": [], "issues": []}}
        )
        github_activity.merge_session_commits(
            "my-repo",
            [_session(session_commits=[{"sha": "bbb2222", "message": "New"}])],
            owner=_builders.OWNER,
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
            date=_builders.jst(2026, 3, 28, 9),
            url=f"https://github.com/{_builders.OWNER}/my-repo/commit/{full_sha}",
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
            owner=_builders.OWNER,
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
                start=_builders.jst(2026, 3, 28, 10),
                session_commits=[
                    {
                        "sha": "aaa1111",
                        "message": "First",
                        "timestamp": _builders.jst(2026, 3, 28, 10, 30),
                    },
                ],
            ),
            _session(
                start=_builders.jst(2026, 3, 28, 12),
                session_commits=[
                    {
                        "sha": "aaa1111",
                        "message": "Duplicate",
                        "timestamp": _builders.jst(2026, 3, 28, 12, 30),
                    },
                ],
            ),
        ]
        github_activity.merge_session_commits(
            "my-repo",
            sessions,
            owner=_builders.OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = github_activity.repos()["my-repo"]["commits"]
        assert len(commits) == 1
        # First-occurrence wins; the second session's duplicate is dropped entirely
        assert commits[0].message == "First"
        assert commits[0].date == _builders.jst(2026, 3, 28, 10, 30)

    def test_uses_per_commit_timestamp_when_present(self):
        github_activity = activity.GitHubActivity({})
        github_activity.merge_session_commits(
            "my-repo",
            [
                _session(
                    start=_builders.jst(2026, 3, 28, 10),
                    session_commits=[
                        {
                            "sha": "aaa",
                            "message": "C1",
                            "timestamp": _builders.jst(2026, 3, 28, 10, 45),
                        },
                        {"sha": "bbb", "message": "C2"},
                    ],
                )
            ],
            owner=_builders.OWNER,
            populate_pull_numbers=self._populate_no_op,
        )
        commits = github_activity.repos()["my-repo"]["commits"]
        # Per-commit timestamp wins; legacy entry falls back to the session start_time
        by_sha = {c.sha: c for c in commits}
        assert by_sha["aaa"].date == _builders.jst(2026, 3, 28, 10, 45)
        assert by_sha["bbb"].date == _builders.jst(2026, 3, 28, 10)
