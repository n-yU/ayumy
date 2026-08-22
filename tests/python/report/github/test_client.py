"""Tests for GitHubClient API wrappers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException, UnknownObjectException

from config import CONFIG
from report import JST

from .._builders import (
    OWNER,
    make_commit,
    make_commit_mock,
    make_issue_mock,
    make_number_mock,
    make_pull_mock,
)

# Default JST day window used across most tests
SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
FULL_NAME = f"{OWNER}/my-repo"


class TestFetchCommits:
    def test_extracts_commit_info(self, github_client, repo):
        github_client.g.search_commits.return_value = [
            make_commit_mock(
                sha="abc123",
                message="Fix bug\n\nDetailed description",
                date=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
            )
        ]
        repo.get_commit.return_value.get_pulls.return_value = []

        result = github_client.fetch_commits(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].sha == "abc123"
        assert result[0].message == "Fix bug"
        assert result[0].author == "user"
        assert result[0].url == f"https://github.com/{FULL_NAME}/commit/abc123"
        assert result[0].pull_numbers == ()

    def test_populates_pull_numbers_from_associated_prs(self, github_client, repo):
        github_client.g.search_commits.return_value = [make_commit_mock(sha="abc123")]
        repo.get_commit.return_value.get_pulls.return_value = [
            make_number_mock(5),
            make_number_mock(9),
        ]

        result = github_client.fetch_commits(repo, SINCE, UNTIL)

        assert result[0].pull_numbers == (5, 9)
        repo.get_commit.assert_called_once_with("abc123")

    def test_widens_query_one_day_each_side_for_utc_safety(self, github_client, repo):
        github_client.g.search_commits.return_value = []

        github_client.fetch_commits(repo, SINCE, UNTIL)

        query = github_client.g.search_commits.call_args[0][0]
        assert f"repo:{FULL_NAME}" in query
        assert "author-date:2026-03-27..2026-03-29" in query

    def test_widens_query_for_partial_day(self, github_client, repo):
        github_client.g.search_commits.return_value = []

        github_client.fetch_commits(
            repo, SINCE, datetime(2026, 3, 28, 15, 0, tzinfo=JST)
        )

        query = github_client.g.search_commits.call_args[0][0]
        assert "author-date:2026-03-27..2026-03-28" in query

    def test_filters_commits_outside_time_range(self, github_client, repo):
        github_client.g.search_commits.return_value = [
            make_commit_mock(sha="aaa", date=datetime(2026, 3, 28, 10, 0, tzinfo=JST)),
            make_commit_mock(sha="bbb", date=datetime(2026, 3, 28, 18, 0, tzinfo=JST)),
        ]
        repo.get_commit.return_value.get_pulls.return_value = []

        result = github_client.fetch_commits(
            repo, SINCE, datetime(2026, 3, 28, 15, 0, tzinfo=JST)
        )

        assert len(result) == 1
        assert result[0].sha == "aaa"


class TestFetchPulls:
    def test_determines_merged_state(self, github_client, repo):
        repo.get_pulls.return_value = [
            make_pull_mock(
                1,
                title="Add feature",
                created_at=datetime(2026, 3, 27, 9, 0, tzinfo=JST),
                updated_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                merged_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
            )
        ]

        result = github_client.fetch_pulls(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].state == "merged"
        assert result[0].draft is False
        assert result[0].url == f"https://github.com/{FULL_NAME}/pull/1"
        assert result[0].created_at == "2026-03-27T09:00:00+09:00"
        assert result[0].merged_at == "2026-03-28T10:00:00+09:00"
        assert result[0].closed_at == "2026-03-28T10:00:00+09:00"

    def test_determines_closed_state(self, github_client, repo):
        repo.get_pulls.return_value = [
            make_pull_mock(
                2,
                created_at=datetime(2026, 3, 27, 9, 0, tzinfo=JST),
                updated_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                closed_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                state="closed",
            )
        ]

        result = github_client.fetch_pulls(repo, SINCE, UNTIL)

        assert result[0].state == "closed"
        assert result[0].merged_at is None
        assert result[0].closed_at == "2026-03-28T10:00:00+09:00"

    def test_determines_open_state(self, github_client, repo):
        repo.get_pulls.return_value = [
            make_pull_mock(
                3,
                created_at=datetime(2026, 3, 28, 9, 0, tzinfo=JST),
                updated_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                state="open",
                draft=True,
            )
        ]

        result = github_client.fetch_pulls(repo, SINCE, UNTIL)

        assert result[0].state == "open"
        assert result[0].draft is True
        assert result[0].closed_at is None

    def test_breaks_on_old_prs(self, github_client, repo):
        repo.get_pulls.return_value = [
            make_pull_mock(
                99,
                created_at=datetime(2026, 3, 26, 0, 0, tzinfo=JST),
                updated_at=datetime(2026, 3, 27, 0, 0, tzinfo=JST),
            )
        ]

        assert github_client.fetch_pulls(repo, SINCE, UNTIL) == []


class TestFetchIssues:
    def test_excludes_pull_requests(self, github_client, repo):
        pr_as_issue = MagicMock()
        pr_as_issue.pull_request = MagicMock()
        repo.get_issues.return_value = [
            make_issue_mock(5, created_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST)),
            pr_as_issue,
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].number == 5
        assert result[0].url == f"https://github.com/{FULL_NAME}/issues/5"
        assert result[0].created_at == "2026-03-28T10:00:00+09:00"
        assert result[0].closed_at is None

    def test_extracts_labels(self, github_client, repo):
        repo.get_issues.return_value = [
            make_issue_mock(
                6,
                created_at=datetime(2026, 3, 28, 9, 0, tzinfo=JST),
                updated_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                closed_at=datetime(2026, 3, 28, 10, 0, tzinfo=JST),
                state_reason="completed",
                labels=("bug",),
            )
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL)

        assert result[0].labels == ("bug",)
        assert result[0].closed_at == "2026-03-28T10:00:00+09:00"
        assert result[0].state_reason == "completed"


class TestFetchActivity:
    @patch("report.github.client.time.sleep")
    @patch("report.github.client.time.time")
    def test_sleeps_remaining_window_time(
        self, mock_time, mock_sleep, github_client, activity_repo
    ):
        """Sleeps only the remaining window time when batch limit is hit."""
        # Batch limit already reached, window opened at t=100, now t=105 → elapsed 5
        github_client._search_count = CONFIG.github.search_batch
        github_client._window_start = 100
        mock_time.return_value = 105
        github_client.g.search_commits.return_value = []
        activity_repo.get_pulls.return_value = []
        activity_repo.get_issues.return_value = []

        github_client.fetch_activity(SINCE, UNTIL, ["repo-0"])

        mock_sleep.assert_called_once_with(CONFIG.github.search_window_sec - 5)
        assert github_client._search_count == 1

    @patch("report.github.client.time.sleep")
    @patch("report.github.client.time.time")
    def test_skips_sleep_when_window_elapsed(
        self, mock_time, mock_sleep, github_client, activity_repo
    ):
        """Skips sleep when enough time has passed since window start."""
        github_client._search_count = CONFIG.github.search_batch
        github_client._window_start = 100
        mock_time.return_value = 125
        github_client.g.search_commits.return_value = []
        activity_repo.get_pulls.return_value = []
        activity_repo.get_issues.return_value = []

        github_client.fetch_activity(SINCE, UNTIL, ["repo-0"])

        mock_sleep.assert_not_called()
        assert github_client._search_count == 1

    @patch("report.github.client.time.sleep")
    @patch("report.github.client.time.time")
    def test_window_starts_on_first_request(
        self, mock_time, mock_sleep, github_client, activity_repo
    ):
        """Window starts when first search request is made, not at init."""
        mock_time.return_value = 500
        github_client.g.search_commits.return_value = []
        activity_repo.get_pulls.return_value = []
        activity_repo.get_issues.return_value = []

        github_client.fetch_activity(SINCE, UNTIL, ["repo-0"])

        assert github_client._window_start == 500
        mock_sleep.assert_not_called()


class TestSearchPullsByEvent:
    def test_query_includes_kind_event_and_widened_range(self, github_client, repo):
        github_client.g.search_issues.return_value = []

        github_client._search_pulls_by_event(repo, SINCE, UNTIL, "merged")

        query = github_client.g.search_issues.call_args[0][0]
        assert f"repo:{FULL_NAME}" in query
        assert "is:pr" in query
        # Range starts at SINCE - 1day; UNTIL is the literal date
        assert "merged:2026-03-27..2026-03-29" in query

    def test_increments_search_throttle_counter(self, github_client, repo):
        github_client.g.search_issues.return_value = []

        github_client._search_pulls_by_event(repo, SINCE, UNTIL, "created")

        assert github_client._search_count == 1


class TestSearchIssuesByEvent:
    def test_query_uses_issue_kind(self, github_client, repo):
        github_client.g.search_issues.return_value = []

        github_client._search_issues_by_event(repo, SINCE, UNTIL, "closed")

        query = github_client.g.search_issues.call_args[0][0]
        assert "is:issue" in query
        assert "closed:2026-03-27..2026-03-29" in query


class TestFetchPullsForCommit:
    def test_returns_pr_numbers(self, github_client, repo):
        commit = MagicMock()
        commit.get_pulls.return_value = [make_number_mock(7), make_number_mock(12)]
        repo.get_commit.return_value = commit

        result = github_client._fetch_pulls_for_commit(repo, "abc1234")

        assert result == [7, 12]
        repo.get_commit.assert_called_once_with("abc1234")

    def test_returns_empty_on_404(self, github_client, repo):
        repo.get_commit.side_effect = UnknownObjectException(404, "Not Found", {})

        assert github_client._fetch_pulls_for_commit(repo, "abc1234") == []

    def test_propagates_non_404_errors(self, github_client, repo):
        repo.get_commit.side_effect = RuntimeError("transient failure")

        with pytest.raises(RuntimeError):
            github_client._fetch_pulls_for_commit(repo, "abc1234")


class TestPopulateCommitPullNumbers:
    @pytest.fixture(autouse=True)
    def _lookup_repo(self, github_client, repo):
        github_client.g.get_user.return_value.get_repo.return_value = repo

    def _commit(self, sha, pull_numbers):
        return make_commit(sha=sha, pull_numbers=pull_numbers)

    def test_skips_commits_with_existing_pull_numbers(self, github_client, repo):
        commits = [self._commit("aaa", [3]), self._commit("bbb", [7])]

        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_not_called()
        assert commits[0].pull_numbers == (3,)
        assert commits[1].pull_numbers == (7,)

    def test_resolves_only_unresolved_commits(self, github_client, repo):
        commit_obj = make_commit_mock(sha="bbb")
        commit_obj.get_pulls.return_value = [make_number_mock(11)]
        repo.get_commit.return_value = commit_obj

        commits = [self._commit("aaa", [3]), self._commit("bbb", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_called_once_with("bbb")
        assert commits[0].pull_numbers == (3,)
        assert commits[1].pull_numbers == (11,)

    def test_normalizes_short_sha_to_full(self, github_client, repo):
        full_sha = "bbb2222abcdef1234abcdef1234abcdef12345678"
        commit_obj = make_commit_mock(sha=full_sha)
        commit_obj.get_pulls.return_value = []
        repo.get_commit.return_value = commit_obj

        commits = [self._commit("bbb2222", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_called_once_with("bbb2222")
        assert commits[0].sha == full_sha
        assert commits[0].url == f"https://github.com/{FULL_NAME}/commit/{full_sha}"

    def test_assigns_empty_list_on_404(self, github_client, repo):
        repo.get_commit.side_effect = UnknownObjectException(404, "Not Found", {})

        commits = [self._commit("aaa", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        assert commits[0].pull_numbers == ()

    def test_assigns_empty_list_on_422(self, github_client, repo):
        # Short SHA ambiguity / not-found is reported as 422 by GET /commits/{sha}
        repo.get_commit.side_effect = GithubException(
            422, {"message": "No commit found for SHA: aaa"}, {}
        )

        commits = [self._commit("aaa", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        assert commits[0].pull_numbers == ()

    def test_propagates_other_github_errors(self, github_client, repo):
        repo.get_commit.side_effect = GithubException(
            500, {"message": "server error"}, {}
        )

        commits = [self._commit("aaa", [])]
        with pytest.raises(GithubException):
            github_client.populate_commit_pull_numbers("repo", commits)

    def test_skips_api_call_when_no_unresolved(self, github_client):
        github_client.populate_commit_pull_numbers("repo", [self._commit("aaa", [3])])

        github_client.g.get_user.assert_not_called()


class TestFetchPullsBackfill:
    def test_unions_search_events_and_commit_derived_pulls(self, github_client, repo):
        # Search returns: created→#1, merged→#2, closed→#3
        github_client.g.search_issues.side_effect = [
            [make_number_mock(1)],
            [make_number_mock(2)],
            [make_number_mock(3)],
        ]
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        repo.get_pull.side_effect = lambda n: make_pull_mock(n, created_at=in_range)

        # Commits already carry pull_numbers populated by fetch_commits
        commits = [
            make_commit(sha="deadbee", date=in_range.isoformat(), pull_numbers=[1, 4])
        ]
        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=commits
        )

        assert sorted(r.number for r in result) == [1, 2, 3, 4]
        # Hybrid path no longer calls get_commit (data comes from pull_numbers)
        repo.get_commit.assert_not_called()

    def test_post_filters_search_only_pulls_outside_range(self, github_client, repo):
        """Search-derived PRs without an in-range event are dropped."""
        github_client.g.search_issues.side_effect = [[make_number_mock(10)], [], []]
        # PR #10 was created on a different day (search widens window by 1 day)
        repo.get_pull.return_value = make_pull_mock(
            10, created_at=datetime(2026, 3, 27, 23, 0, tzinfo=JST)
        )

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[]
        )

        assert result == []

    def test_keeps_commit_derived_pull_without_in_range_event(
        self, github_client, repo
    ):
        """Commit-derived PRs are kept regardless of state-event timing."""
        github_client.g.search_issues.side_effect = [[], [], []]
        # PR opened weeks ago, no merged/closed yet
        repo.get_pull.return_value = make_pull_mock(
            99, created_at=datetime(2026, 3, 1, 0, 0, tzinfo=JST)
        )

        commits = [make_commit(sha="abc", pull_numbers=[99])]
        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=commits
        )

        assert [r.number for r in result] == [99]

    def test_skips_pull_not_found(self, github_client, repo):
        github_client.g.search_issues.side_effect = [[make_number_mock(50)], [], []]
        repo.get_pull.side_effect = UnknownObjectException(404, "Not Found", {})

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[]
        )

        assert result == []

    def test_propagates_non_404_pull_fetch_errors(self, github_client, repo):
        github_client.g.search_issues.side_effect = [[make_number_mock(50)], [], []]
        repo.get_pull.side_effect = RuntimeError("transient failure")

        with pytest.raises(RuntimeError):
            github_client.fetch_pulls(repo, SINCE, UNTIL, is_backfill=True, commits=[])

    def test_keeps_session_derived_pull_without_in_range_event(
        self, github_client, repo
    ):
        """Session-derived PRs are kept regardless of state-event timing."""
        github_client.g.search_issues.side_effect = [[], [], []]
        repo.get_pull.return_value = make_pull_mock(
            77, created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST)
        )

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[], session_numbers=[77]
        )

        assert [r.number for r in result] == [77]

    def test_unions_session_with_search_and_dedups(self, github_client, repo):
        # Search→#10 (in range), session→#10, #20
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        github_client.g.search_issues.side_effect = [[make_number_mock(10)], [], []]
        repo.get_pull.side_effect = lambda n: make_pull_mock(n, created_at=in_range)

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[], session_numbers=[10, 20]
        )

        assert sorted(r.number for r in result) == [10, 20]
        # #10 was fetched once (event ∪ session uses sorted unique numbers)
        called = [c.args[0] for c in repo.get_pull.call_args_list]
        assert called == sorted(called) and len(called) == 2

    def test_skips_session_pull_not_found(self, github_client, repo):
        github_client.g.search_issues.side_effect = [[], [], []]
        repo.get_pull.side_effect = UnknownObjectException(404, "Not Found", {})

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[], session_numbers=[999]
        )

        assert result == []


class TestFetchIssuesBackfill:
    def test_unions_created_and_closed_search(self, github_client, repo):
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        github_client.g.search_issues.side_effect = [
            [make_issue_mock(1, created_at=in_range)],
            [
                make_issue_mock(
                    2,
                    created_at=datetime(2026, 3, 1, 0, 0, tzinfo=JST),
                    closed_at=in_range,
                    state_reason="completed",
                )
            ],
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL, is_backfill=True)

        assert sorted(r.number for r in result) == [1, 2]
        assert result[1].state_reason == "completed"

    def test_post_filters_issue_outside_range(self, github_client, repo):
        # Returned by search but actually outside JST window
        github_client.g.search_issues.side_effect = [
            [make_issue_mock(5, created_at=datetime(2026, 3, 27, 22, 0, tzinfo=JST))],
            [],
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL, is_backfill=True)

        assert result == []

    def test_keeps_session_derived_issue_without_in_range_event(
        self, github_client, repo
    ):
        """Session-derived issues are kept regardless of state-event timing."""
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = make_issue_mock(
            42, created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST)
        )

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[42]
        )

        assert [r.number for r in result] == [42]

    def test_skips_session_number_resolving_to_pr(self, github_client, repo):
        """Session #N may resolve to a PR; PRs must be filtered out."""
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = make_issue_mock(
            87,
            created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST),
            pull_request=MagicMock(),
        )

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[87]
        )

        assert result == []

    def test_skips_session_issue_not_found(self, github_client, repo):
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.side_effect = UnknownObjectException(404, "Not Found", {})

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[999]
        )

        assert result == []

    def test_does_not_refetch_session_number_already_in_search(
        self, github_client, repo
    ):
        """Numbers covered by Search are reused; get_issue is only called for new ones."""
        github_client.g.search_issues.side_effect = [
            [make_issue_mock(10, created_at=datetime(2026, 3, 28, 12, 0, tzinfo=JST))],
            [],
        ]

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[10]
        )

        assert [r.number for r in result] == [10]
        repo.get_issue.assert_not_called()


class TestFetchActivityBackfill:
    def test_propagates_is_backfill_flag(self, github_client, activity_repo):
        github_client.g.search_commits.return_value = []
        # Hybrid path uses search_issues, not get_pulls/get_issues
        github_client.g.search_issues.return_value = []

        github_client.fetch_activity(SINCE, UNTIL, ["repo"], is_backfill=True)

        # Default updated_at path must NOT be invoked under backfill
        activity_repo.get_pulls.assert_not_called()
        activity_repo.get_issues.assert_not_called()
        # 5 search calls expected (created/merged/closed PR + created/closed issue)
        assert github_client.g.search_issues.call_count == 5
