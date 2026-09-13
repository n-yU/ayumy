"""Tests for the GitHub API wrappers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import github as gh
import pytest

from config import CONFIG
from report.shared import dates

from .. import _builders

# Default JST day window used across most tests
SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=dates.JST)


class TestFetchCommits:
    def test_extracts_commit_info(self, github_client, repo):
        github_client.g.search_commits.return_value = [
            _builders.commit_mock(
                sha="abc123",
                message="Fix bug\n\nDetailed description",
                date=datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST),
            )
        ]
        repo.get_commit.return_value.get_pulls.return_value = []

        result = github_client.fetch_commits(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].sha == "abc123"
        assert result[0].message == "Fix bug"
        assert result[0].author == "user"
        assert (
            result[0].url
            == f"https://github.com/{_builders.REPO_FULL_NAME}/commit/abc123"
        )
        assert result[0].pull_numbers == ()

    def test_populates_pull_numbers_from_associated_prs(self, github_client, repo):
        github_client.g.search_commits.return_value = [
            _builders.commit_mock(sha="abc123")
        ]
        repo.get_commit.return_value.get_pulls.return_value = [
            _builders.number_mock(5),
            _builders.number_mock(9),
        ]

        result = github_client.fetch_commits(repo, SINCE, UNTIL)

        assert result[0].pull_numbers == (5, 9)
        repo.get_commit.assert_called_once_with("abc123")

    @pytest.mark.parametrize(
        ("until", "expected_range"),
        [
            pytest.param(UNTIL, "2026-03-27..2026-03-29", id="full_day"),
            pytest.param(
                datetime(2026, 3, 28, 15, 0, tzinfo=dates.JST),
                "2026-03-27..2026-03-28",
                id="partial_day",
            ),
        ],
    )
    def test_widens_query_one_day_each_side_for_utc_safety(
        self, github_client, repo, until, expected_range
    ):
        github_client.g.search_commits.return_value = []

        github_client.fetch_commits(repo, SINCE, until)

        query = github_client.g.search_commits.call_args[0][0]
        assert f"repo:{_builders.REPO_FULL_NAME}" in query
        assert f"author-date:{expected_range}" in query

    def test_filters_commits_outside_time_range(self, github_client, repo):
        github_client.g.search_commits.return_value = [
            _builders.commit_mock(
                sha="aaa", date=datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST)
            ),
            _builders.commit_mock(
                sha="bbb", date=datetime(2026, 3, 28, 18, 0, tzinfo=dates.JST)
            ),
        ]
        repo.get_commit.return_value.get_pulls.return_value = []

        result = github_client.fetch_commits(
            repo, SINCE, datetime(2026, 3, 28, 15, 0, tzinfo=dates.JST)
        )

        assert len(result) == 1
        assert result[0].sha == "aaa"


class TestFetchPullCommits:
    def test_tags_each_commit_with_pull_number(self, github_client, repo):
        repo.get_pull.return_value.get_commits.return_value = [
            _builders.commit_mock(sha="aaa"),
            _builders.commit_mock(sha="bbb"),
        ]

        result = github_client.fetch_pull_commits(repo, 7, SINCE, UNTIL)

        repo.get_pull.assert_called_once_with(7)
        assert [c.sha for c in result] == ["aaa", "bbb"]
        assert all(c.pull_numbers == (7,) for c in result)
        # Not a Search API call, so it must not consume the search throttle budget
        assert github_client._search_count == 0

    def test_filters_commits_outside_window(self, github_client, repo):
        repo.get_pull.return_value.get_commits.return_value = [
            _builders.commit_mock(
                sha="before", date=datetime(2026, 3, 27, 23, 59, tzinfo=dates.JST)
            ),
            _builders.commit_mock(sha="start", date=SINCE),
            _builders.commit_mock(sha="end", date=UNTIL),
        ]

        result = github_client.fetch_pull_commits(repo, 7, SINCE, UNTIL)

        assert [c.sha for c in result] == ["start"]


class TestFetchPulls:
    def test_returns_pull_info_for_pr_updated_in_window(self, github_client, repo):
        repo.get_pulls.return_value = [
            _builders.pull_mock(
                1,
                title="Add feature",
                created_at=datetime(2026, 3, 27, 9, 0, tzinfo=dates.JST),
                updated_at=datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST),
                merged_at=datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST),
            )
        ]

        result = github_client.fetch_pulls(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].state == "merged"
        assert result[0].draft is False
        assert result[0].url == f"https://github.com/{_builders.REPO_FULL_NAME}/pull/1"
        assert result[0].created_at == "2026-03-27T09:00:00+09:00"
        assert result[0].merged_at == "2026-03-28T10:00:00+09:00"
        assert result[0].closed_at == "2026-03-28T10:00:00+09:00"

    def test_breaks_on_old_prs(self, github_client, repo):
        repo.get_pulls.return_value = [
            _builders.pull_mock(
                99,
                created_at=datetime(2026, 3, 26, 0, 0, tzinfo=dates.JST),
                updated_at=datetime(2026, 3, 27, 0, 0, tzinfo=dates.JST),
            )
        ]

        assert github_client.fetch_pulls(repo, SINCE, UNTIL) == []


class TestFetchIssues:
    def test_excludes_pull_requests(self, github_client, repo):
        pr_as_issue = MagicMock()
        pr_as_issue.pull_request = MagicMock()
        repo.get_issues.return_value = [
            _builders.issue_mock(
                5, created_at=datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST)
            ),
            pr_as_issue,
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL)

        assert len(result) == 1
        assert result[0].number == 5
        assert (
            result[0].url == f"https://github.com/{_builders.REPO_FULL_NAME}/issues/5"
        )
        assert result[0].created_at == "2026-03-28T10:00:00+09:00"
        assert result[0].closed_at is None


class TestFetchActivity:
    @pytest.mark.parametrize(
        ("search_count", "window_start", "now", "expected_sleep"),
        [
            pytest.param(
                CONFIG.github.search_batch,
                100,
                105,
                CONFIG.github.search_window_sec - 5,
                id="sleeps_remaining_window_time",
            ),
            pytest.param(
                CONFIG.github.search_batch, 100, 125, None, id="window_already_elapsed"
            ),
            pytest.param(0, 0.0, 500, None, id="first_request"),
        ],
    )
    @patch("report.github.client.time.sleep")
    @patch("report.github.client.time.time")
    def test_throttles_search_requests(
        self,
        mock_time,
        mock_sleep,
        github_client,
        activity_repo,
        search_count,
        window_start,
        now,
        expected_sleep,
    ):
        github_client._search_count = search_count
        github_client._window_start = window_start
        mock_time.return_value = now
        github_client.g.search_commits.return_value = []
        activity_repo.get_pulls.return_value = []
        activity_repo.get_issues.return_value = []

        github_client.fetch_activity(SINCE, UNTIL, ["repo-0"])

        if expected_sleep is None:
            mock_sleep.assert_not_called()
        else:
            mock_sleep.assert_called_once_with(expected_sleep)
        # The window restarts at the request that opens it, not at client init
        assert github_client._window_start == now
        assert github_client._search_count == 1


class TestSearchByEvent:
    @pytest.mark.parametrize(
        ("method", "event", "expected_kind"),
        [
            pytest.param("_search_pulls_by_event", "merged", "is:pr", id="pull"),
            pytest.param("_search_issues_by_event", "closed", "is:issue", id="issue"),
        ],
    )
    def test_query_includes_kind_event_and_widened_range(
        self, github_client, repo, method, event, expected_kind
    ):
        github_client.g.search_issues.return_value = []

        getattr(github_client, method)(repo, SINCE, UNTIL, event)

        query = github_client.g.search_issues.call_args[0][0]
        assert f"repo:{_builders.REPO_FULL_NAME}" in query
        assert expected_kind in query
        # Range starts at SINCE - 1day; UNTIL is the literal date
        assert f"{event}:2026-03-27..2026-03-29" in query

    def test_increments_search_throttle_counter(self, github_client, repo):
        github_client.g.search_issues.return_value = []

        github_client._search_pulls_by_event(repo, SINCE, UNTIL, "created")

        assert github_client._search_count == 1


class TestFetchPullsForCommit:
    def test_returns_empty_on_404(self, github_client, repo):
        repo.get_commit.side_effect = gh.UnknownObjectException(404, "Not Found", {})

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
        return _builders.commit(sha=sha, pull_numbers=pull_numbers)

    def test_skips_commits_with_existing_pull_numbers(self, github_client, repo):
        commits = [self._commit("aaa", [3]), self._commit("bbb", [7])]

        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_not_called()
        assert commits[0].pull_numbers == (3,)
        assert commits[1].pull_numbers == (7,)

    def test_resolves_only_unresolved_commits(self, github_client, repo):
        commit_obj = _builders.commit_mock(sha="bbb")
        commit_obj.get_pulls.return_value = [_builders.number_mock(11)]
        repo.get_commit.return_value = commit_obj

        commits = [self._commit("aaa", [3]), self._commit("bbb", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_called_once_with("bbb")
        assert commits[0].pull_numbers == (3,)
        assert commits[1].pull_numbers == (11,)

    def test_normalizes_short_sha_to_full(self, github_client, repo):
        full_sha = "bbb2222abcdef1234abcdef1234abcdef12345678"
        commit_obj = _builders.commit_mock(sha=full_sha)
        commit_obj.get_pulls.return_value = []
        repo.get_commit.return_value = commit_obj

        commits = [self._commit("bbb2222", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        repo.get_commit.assert_called_once_with("bbb2222")
        assert commits[0].sha == full_sha
        assert (
            commits[0].url
            == f"https://github.com/{_builders.REPO_FULL_NAME}/commit/{full_sha}"
        )

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(gh.UnknownObjectException(404, "Not Found", {}), id="404"),
            # Short SHA ambiguity / not-found is reported as 422 by GET /commits/{sha}
            pytest.param(
                gh.GithubException(
                    422, {"message": "No commit found for SHA: aaa"}, {}
                ),
                id="422",
            ),
        ],
    )
    def test_assigns_empty_list_on_missing_commit(self, github_client, repo, error):
        repo.get_commit.side_effect = error

        commits = [self._commit("aaa", [])]
        github_client.populate_commit_pull_numbers("repo", commits)

        assert commits[0].pull_numbers == ()

    def test_propagates_other_github_errors(self, github_client, repo):
        repo.get_commit.side_effect = gh.GithubException(
            500, {"message": "server error"}, {}
        )

        commits = [self._commit("aaa", [])]
        with pytest.raises(gh.GithubException):
            github_client.populate_commit_pull_numbers("repo", commits)

    def test_skips_api_call_when_no_unresolved(self, github_client):
        github_client.populate_commit_pull_numbers("repo", [self._commit("aaa", [3])])

        github_client.g.get_user.assert_not_called()


class TestFetchPullsBackfill:
    def test_unions_search_events_and_commit_derived_pulls(self, github_client, repo):
        # Search returns: created→#1, merged→#2, closed→#3
        github_client.g.search_issues.side_effect = [
            [_builders.number_mock(1)],
            [_builders.number_mock(2)],
            [_builders.number_mock(3)],
        ]
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=dates.JST)
        repo.get_pull.side_effect = lambda n: _builders.pull_mock(
            n, created_at=in_range
        )

        # Commits already carry pull_numbers populated by fetch_commits
        commits = [
            _builders.commit(
                sha="deadbee", date=in_range.isoformat(), pull_numbers=[1, 4]
            )
        ]
        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=commits
        )

        assert sorted(r.number for r in result) == [1, 2, 3, 4]
        # Hybrid path no longer calls get_commit (data comes from pull_numbers)
        repo.get_commit.assert_not_called()

    def test_post_filters_search_only_pulls_outside_range(self, github_client, repo):
        """Search-derived PRs without an in-range event are dropped."""
        github_client.g.search_issues.side_effect = [
            [_builders.number_mock(10)],
            [],
            [],
        ]
        # PR #10 was created on a different day (search widens window by 1 day)
        repo.get_pull.return_value = _builders.pull_mock(
            10, created_at=datetime(2026, 3, 27, 23, 0, tzinfo=dates.JST)
        )

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[]
        )

        assert result == []

    @pytest.mark.parametrize(
        ("number", "extra_kwargs"),
        [
            pytest.param(
                99,
                {"commits": [_builders.commit(sha="abc", pull_numbers=[99])]},
                id="commit_derived",
            ),
            pytest.param(
                77, {"commits": [], "session_numbers": [77]}, id="session_derived"
            ),
        ],
    )
    def test_keeps_derived_pull_without_in_range_event(
        self, github_client, repo, number, extra_kwargs
    ):
        """PRs reached via commits or sessions are kept regardless of state-event timing."""
        github_client.g.search_issues.side_effect = [[], [], []]
        # PR opened weeks ago, no merged/closed yet
        repo.get_pull.return_value = _builders.pull_mock(
            number, created_at=datetime(2026, 3, 1, 0, 0, tzinfo=dates.JST)
        )

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, **extra_kwargs
        )

        assert [r.number for r in result] == [number]

    @pytest.mark.parametrize(
        ("search_results", "extra_kwargs"),
        [
            pytest.param(
                [[_builders.number_mock(50)], [], []], {}, id="search_derived"
            ),
            pytest.param(
                [[], [], []], {"session_numbers": [999]}, id="session_derived"
            ),
        ],
    )
    def test_skips_pull_not_found(
        self, github_client, repo, search_results, extra_kwargs
    ):
        github_client.g.search_issues.side_effect = search_results
        repo.get_pull.side_effect = gh.UnknownObjectException(404, "Not Found", {})

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[], **extra_kwargs
        )

        assert result == []

    def test_propagates_non_404_pull_fetch_errors(self, github_client, repo):
        github_client.g.search_issues.side_effect = [
            [_builders.number_mock(50)],
            [],
            [],
        ]
        repo.get_pull.side_effect = RuntimeError("transient failure")

        with pytest.raises(RuntimeError):
            github_client.fetch_pulls(repo, SINCE, UNTIL, is_backfill=True, commits=[])

    def test_unions_session_with_search_and_dedups(self, github_client, repo):
        # Search→#10 (in range), session→#10, #20
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=dates.JST)
        github_client.g.search_issues.side_effect = [
            [_builders.number_mock(10)],
            [],
            [],
        ]
        repo.get_pull.side_effect = lambda n: _builders.pull_mock(
            n, created_at=in_range
        )

        result = github_client.fetch_pulls(
            repo, SINCE, UNTIL, is_backfill=True, commits=[], session_numbers=[10, 20]
        )

        assert sorted(r.number for r in result) == [10, 20]
        # #10 was fetched once (event ∪ session uses sorted unique numbers)
        called = [c.args[0] for c in repo.get_pull.call_args_list]
        assert called == sorted(called) and len(called) == 2


class TestFetchIssuesBackfill:
    def test_unions_created_and_closed_search(self, github_client, repo):
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=dates.JST)
        github_client.g.search_issues.side_effect = [
            [_builders.issue_mock(1, created_at=in_range)],
            [
                _builders.issue_mock(
                    2,
                    created_at=datetime(2026, 3, 1, 0, 0, tzinfo=dates.JST),
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
            [
                _builders.issue_mock(
                    5, created_at=datetime(2026, 3, 27, 22, 0, tzinfo=dates.JST)
                )
            ],
            [],
        ]

        result = github_client.fetch_issues(repo, SINCE, UNTIL, is_backfill=True)

        assert result == []

    def test_keeps_session_derived_issue_without_in_range_event(
        self, github_client, repo
    ):
        """Session-derived issues are kept regardless of state-event timing."""
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = _builders.issue_mock(
            42, created_at=datetime(2026, 2, 1, 0, 0, tzinfo=dates.JST)
        )

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[42]
        )

        assert [r.number for r in result] == [42]

    def test_skips_session_number_resolving_to_pr(self, github_client, repo):
        """Session #N may resolve to a PR; PRs must be filtered out."""
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = _builders.issue_mock(
            87,
            created_at=datetime(2026, 2, 1, 0, 0, tzinfo=dates.JST),
            pull_request=MagicMock(),
        )

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[87]
        )

        assert result == []

    def test_skips_session_issue_not_found(self, github_client, repo):
        github_client.g.search_issues.side_effect = [[], []]
        repo.get_issue.side_effect = gh.UnknownObjectException(404, "Not Found", {})

        result = github_client.fetch_issues(
            repo, SINCE, UNTIL, is_backfill=True, session_numbers=[999]
        )

        assert result == []

    def test_does_not_refetch_session_number_already_in_search(
        self, github_client, repo
    ):
        """Numbers covered by Search are reused; get_issue is only called for new ones."""
        github_client.g.search_issues.side_effect = [
            [
                _builders.issue_mock(
                    10, created_at=datetime(2026, 3, 28, 12, 0, tzinfo=dates.JST)
                )
            ],
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


class TestFetchActivityPullCommits:
    @pytest.fixture(autouse=True)
    def _no_issues(self, activity_repo):
        activity_repo.get_issues.return_value = []

    def test_adds_pull_commits_missing_from_search(self, github_client, activity_repo):
        github_client.g.search_commits.return_value = []
        activity_repo.get_pulls.return_value = [_builders.pull_mock(200)]
        activity_repo.get_pull.return_value.get_commits.return_value = [
            _builders.commit_mock(sha="aaa")
        ]

        result = github_client.fetch_activity(SINCE, UNTIL, ["repo"])

        commits = result.repos()[_builders.REPO]["commits"]
        assert [(c.sha, c.pull_numbers) for c in commits] == [("aaa", (200,))]

    def test_unions_pull_numbers_for_sha_found_by_both_paths(
        self, github_client, activity_repo
    ):
        github_client.g.search_commits.return_value = [_builders.commit_mock(sha="aaa")]
        activity_repo.get_commit.return_value.get_pulls.return_value = [
            _builders.number_mock(5)
        ]
        activity_repo.get_pulls.return_value = [_builders.pull_mock(7)]
        activity_repo.get_pull.return_value.get_commits.return_value = [
            _builders.commit_mock(sha="aaa")
        ]

        result = github_client.fetch_activity(SINCE, UNTIL, ["repo"])

        commits = result.repos()[_builders.REPO]["commits"]
        assert [(c.sha, c.pull_numbers) for c in commits] == [("aaa", (5, 7))]

    @pytest.mark.parametrize(
        ("search_results", "session_pulls"),
        [
            pytest.param(
                [[_builders.number_mock(200)], [], [], [], []], {}, id="search_derived"
            ),
            pytest.param([[], [], [], [], []], {"repo": [200]}, id="session_derived"),
        ],
    )
    def test_restores_commits_of_backfill_pulls(
        self, github_client, activity_repo, search_results, session_pulls
    ):
        github_client.g.search_commits.return_value = []
        # Hybrid path searches PR events 3 times, then issue events 2 times
        github_client.g.search_issues.side_effect = search_results
        activity_repo.get_pull.return_value = _builders.pull_mock(200)
        activity_repo.get_pull.return_value.get_commits.return_value = [
            _builders.commit_mock(sha="aaa")
        ]

        result = github_client.fetch_activity(
            SINCE, UNTIL, ["repo"], is_backfill=True, session_pulls=session_pulls
        )

        commits = result.repos()[_builders.REPO]["commits"]
        assert [(c.sha, c.pull_numbers) for c in commits] == [("aaa", (200,))]
