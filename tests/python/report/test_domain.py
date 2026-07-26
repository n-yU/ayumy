"""Tests for report.domain dataclasses."""

import dataclasses
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from report import JST
from report.domain import CommitInfo, IssueInfo, PullInfo

SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _commit(**overrides):
    base = {
        "sha": "abc1234deadbeef",
        "message": "Fix bug",
        "author": "user",
        "date": "2026-03-28T10:00:00+09:00",
        "url": "https://github.com/n-yU/my-repo/commit/abc1234deadbeef",
    }
    base.update(overrides)
    return CommitInfo(**base)


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
    return PullInfo(**base)


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
    return IssueInfo(**base)


def _label_mock(name):
    label = MagicMock()
    label.name = name
    return label


def _make_pr_mock(
    *,
    pr_state="open",
    merged_at_dt=None,
    closed_at_dt=None,
    label_names=(),
):
    pr = MagicMock()
    pr.number = 42
    pr.title = "PR title"
    pr.state = pr_state
    pr.user.login = "user"
    pr.labels = [_label_mock(n) for n in label_names]
    pr.draft = False
    pr.html_url = "https://github.com/n-yU/my-repo/pull/42"
    pr.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
    pr.merged_at = merged_at_dt
    pr.closed_at = closed_at_dt if closed_at_dt is not None else merged_at_dt
    pr.merge_commit_sha = "merge-sha"
    return pr


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
        commit = MagicMock()
        commit.sha = "deadbeef"
        commit.commit.message = "Subject line\n\nBody paragraph"
        commit.commit.author.name = "alice"
        commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        commit.html_url = "https://github.com/n-yU/my-repo/commit/deadbeef"

        info = CommitInfo.from_search_commit(commit, pull_numbers=[42, 43])

        assert info.sha == "deadbeef"
        assert info.message == "Subject line"
        assert info.author == "alice"
        assert info.date == "2026-03-28T10:00:00+09:00"
        assert info.url == "https://github.com/n-yU/my-repo/commit/deadbeef"
        assert info.pull_numbers == (42, 43)

    def test_from_search_commit_defaults_pull_numbers_to_empty(self):
        commit = MagicMock()
        commit.sha = "deadbeef"
        commit.commit.message = "Subject"
        commit.commit.author.name = "alice"
        commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        commit.html_url = "https://example/commit/deadbeef"

        info = CommitInfo.from_search_commit(commit)

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
    def test_label_renders_repo_number_title(self):
        pr = _pull(number=42, title="Add feature")
        assert pr.label("my-repo") == "my-repo#42: Add feature"

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
        ],
    )
    def test_state_in_range(self, state, merged_at, closed_at, expected):
        pr = _pull(state=state, merged_at=merged_at, closed_at=closed_at)
        assert pr.state_in_range(SINCE, UNTIL) == expected

    def test_from_pull_request_classifies_merged(self):
        pr = _make_pr_mock(merged_at_dt=datetime(2026, 3, 28, 10, 0, tzinfo=JST))
        info = PullInfo.from_pull_request(pr)
        assert info.state == "merged"
        assert info.merged_at == "2026-03-28T10:00:00+09:00"
        assert info.merge_commit_sha == "merge-sha"

    def test_from_pull_request_classifies_closed_unmerged(self):
        pr = _make_pr_mock(
            pr_state="closed",
            closed_at_dt=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
        )
        info = PullInfo.from_pull_request(pr)
        assert info.state == "closed"
        assert info.merged_at is None
        # Unmerged: ignore the test-merge SHA returned by GitHub
        assert info.merge_commit_sha is None

    def test_from_pull_request_classifies_open(self):
        pr = _make_pr_mock(pr_state="open")
        info = PullInfo.from_pull_request(pr)
        assert info.state == "open"
        assert info.merged_at is None
        assert info.closed_at is None
        assert info.merge_commit_sha is None

    def test_from_pull_request_extracts_labels_as_tuple(self):
        pr = _make_pr_mock(label_names=("bug", "ready"))
        info = PullInfo.from_pull_request(pr)
        assert info.labels == ("bug", "ready")


class TestIssueInfo:
    def test_label_renders_repo_number_title(self):
        issue = _issue(number=7, title="Bug report")
        assert issue.label("my-repo") == "my-repo#7: Bug report"

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
        ],
    )
    def test_state_in_range(self, state, closed_at, expected):
        issue = _issue(state=state, closed_at=closed_at)
        assert issue.state_in_range(SINCE, UNTIL) == expected

    def test_from_issue_extracts_labels_as_tuple(self):
        issue = MagicMock()
        issue.number = 7
        issue.title = "Bug"
        issue.state = "open"
        issue.user.login = "user"
        issue.labels = [_label_mock("bug"), _label_mock("priority:high")]
        issue.html_url = "https://github.com/n-yU/my-repo/issues/7"
        issue.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
        issue.closed_at = None
        issue.state_reason = None

        info = IssueInfo.from_issue(issue)

        assert info.labels == ("bug", "priority:high")
        assert info.closed_at is None
        assert info.state == "open"

    def test_from_issue_serializes_closed_at(self):
        issue = MagicMock()
        issue.number = 7
        issue.title = "Bug"
        issue.state = "closed"
        issue.user.login = "user"
        issue.labels = []
        issue.html_url = "https://github.com/n-yU/my-repo/issues/7"
        issue.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
        issue.closed_at = datetime(2026, 3, 28, 11, 0, tzinfo=JST)
        issue.state_reason = "not_planned"

        info = IssueInfo.from_issue(issue)

        assert info.closed_at == "2026-03-28T11:00:00+09:00"
        assert info.state_reason == "not_planned"
