"""Tests for GitHubClient API wrappers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException, UnknownObjectException

from report import JST
from report.github import _SEARCH_BATCH, _SEARCH_WINDOW, GitHubClient

# Default JST day window used across most tests
SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _make_client():
    """Create a GitHubClient with mocked PyGithub."""
    client = GitHubClient.__new__(GitHubClient)
    client.g = MagicMock()
    client._search_count = 0
    client._window_start = 0.0
    return client


def _make_pr_issue(number):
    """Create a search_issues result mock with just a PR number."""
    item = MagicMock()
    item.number = number
    return item


def _make_commit_mock(
    sha,
    *,
    message,
    date,
    author="user",
    repo_full_name="n-yU/my-repo",
    url=None,
):
    """Create a search_commits result mock with the fields fetch_commits reads."""
    commit = MagicMock()
    commit.sha = sha
    commit.commit.message = message
    commit.commit.author.name = author
    commit.commit.author.date = date
    commit.html_url = url or f"https://github.com/{repo_full_name}/commit/{sha}"
    return commit


def _make_pull(
    number,
    *,
    created_at,
    updated_at=None,
    merged_at=None,
    closed_at=None,
    state=None,
    title="PR",
    labels=None,
    draft=False,
):
    """Create a PullRequest mock with the fields _build_pull_info reads."""
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.created_at = created_at
    pr.updated_at = updated_at if updated_at is not None else created_at
    pr.merged_at = merged_at
    pr.closed_at = closed_at
    if state is None:
        pr.state = "closed" if closed_at else "open"
    else:
        pr.state = state
    pr.draft = draft
    pr.html_url = f"https://github.com/n-yU/repo/pull/{number}"
    pr.user.login = "user"
    pr.labels = labels or []
    return pr


def _make_issue(
    number,
    *,
    created_at,
    updated_at=None,
    closed_at=None,
    state=None,
    state_reason=None,
    title="Issue",
    labels=None,
    pull_request=None,
):
    """Create an Issue mock matching _build_issue_info."""
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.created_at = created_at
    issue.updated_at = updated_at if updated_at is not None else created_at
    issue.closed_at = closed_at
    issue.state = state or ("closed" if closed_at else "open")
    issue.state_reason = state_reason
    issue.html_url = f"https://github.com/n-yU/repo/issues/{number}"
    issue.user.login = "user"
    issue.labels = labels or []
    issue.pull_request = pull_request
    return issue


def _make_activity_repo(client, *, name="repo", full_name="n-yU/repo"):
    """Wire client.g.get_user().get_repo() to return a repo mock and return it."""
    mock_user = MagicMock()
    client.g.get_user.return_value = mock_user
    mock_repo = MagicMock()
    mock_repo.name = name
    mock_repo.full_name = full_name
    mock_user.get_repo.return_value = mock_repo
    return mock_repo


class TestFetchCommits:
    def test_extracts_commit_info(self):
        client = _make_client()

        mock_commit = MagicMock()
        mock_commit.sha = "abc123"
        mock_commit.commit.message = "Fix bug\n\nDetailed description"
        mock_commit.commit.author.name = "user"
        mock_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        mock_commit.html_url = "https://github.com/n-yU/my-repo/commit/abc123"

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = [mock_commit]

        # Commit has no associated PR
        commit_obj = MagicMock()
        commit_obj.get_pulls.return_value = []
        repo.get_commit.return_value = commit_obj

        result = client.fetch_commits(repo, SINCE, UNTIL)
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "user"
        assert result[0]["url"] == "https://github.com/n-yU/my-repo/commit/abc123"
        assert result[0]["pull_numbers"] == []

    def test_populates_pull_numbers_from_associated_prs(self):
        client = _make_client()

        mock_commit = MagicMock()
        mock_commit.sha = "abc123"
        mock_commit.commit.message = "Squash merge"
        mock_commit.commit.author.name = "user"
        mock_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        mock_commit.html_url = "https://github.com/n-yU/my-repo/commit/abc123"

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = [mock_commit]

        commit_obj = MagicMock()
        pr1 = MagicMock()
        pr1.number = 5
        pr2 = MagicMock()
        pr2.number = 9
        commit_obj.get_pulls.return_value = [pr1, pr2]
        repo.get_commit.return_value = commit_obj

        result = client.fetch_commits(repo, SINCE, UNTIL)
        assert result[0]["pull_numbers"] == [5, 9]
        repo.get_commit.assert_called_once_with("abc123")

    def test_widens_query_one_day_each_side_for_utc_safety(self):
        client = _make_client()

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = []

        client.fetch_commits(repo, SINCE, UNTIL)

        query = client.g.search_commits.call_args[0][0]
        assert "repo:n-yU/my-repo" in query
        assert "author-date:2026-03-27..2026-03-29" in query

    def test_widens_query_for_partial_day(self):
        client = _make_client()
        partial_until = datetime(2026, 3, 28, 15, 0, tzinfo=JST)

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = []

        client.fetch_commits(repo, SINCE, partial_until)

        query = client.g.search_commits.call_args[0][0]
        assert "author-date:2026-03-27..2026-03-28" in query

    def test_filters_commits_outside_time_range(self):
        client = _make_client()
        partial_until = datetime(2026, 3, 28, 15, 0, tzinfo=JST)

        in_range = MagicMock()
        in_range.sha = "aaa"
        in_range.commit.message = "Morning commit"
        in_range.commit.author.name = "user"
        in_range.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)

        out_of_range = MagicMock()
        out_of_range.sha = "bbb"
        out_of_range.commit.message = "Evening commit"
        out_of_range.commit.author.name = "user"
        out_of_range.commit.author.date = datetime(2026, 3, 28, 18, 0, tzinfo=JST)

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = [in_range, out_of_range]
        repo.get_commit.return_value.get_pulls.return_value = []

        result = client.fetch_commits(repo, SINCE, partial_until)
        assert len(result) == 1
        assert result[0]["sha"] == "aaa"


class TestFetchPulls:
    def test_determines_merged_state(self):
        client = _make_client()

        pr = MagicMock()
        pr.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.merged_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.closed_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.created_at = datetime(2026, 3, 27, 9, 0, tzinfo=JST)
        pr.draft = False
        pr.html_url = "https://github.com/n-yU/repo/pull/1"
        pr.number = 1
        pr.title = "Add feature"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, SINCE, UNTIL)
        assert len(result) == 1
        assert result[0]["state"] == "merged"
        assert result[0]["draft"] is False
        assert result[0]["url"] == "https://github.com/n-yU/repo/pull/1"
        assert result[0]["created_at"] == "2026-03-27T09:00:00+09:00"
        assert result[0]["merged_at"] == "2026-03-28T10:00:00+09:00"
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"

    def test_determines_closed_state(self):
        client = _make_client()

        pr = MagicMock()
        pr.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.merged_at = None
        pr.closed_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.created_at = datetime(2026, 3, 27, 9, 0, tzinfo=JST)
        pr.draft = False
        pr.html_url = "https://github.com/n-yU/repo/pull/2"
        pr.state = "closed"
        pr.number = 2
        pr.title = "Rejected PR"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, SINCE, UNTIL)
        assert result[0]["state"] == "closed"
        assert result[0]["merged_at"] is None
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"

    def test_determines_open_state(self):
        client = _make_client()

        pr = MagicMock()
        pr.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.merged_at = None
        pr.closed_at = None
        pr.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
        pr.draft = True
        pr.html_url = "https://github.com/n-yU/repo/pull/3"
        pr.state = "open"
        pr.number = 3
        pr.title = "WIP"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, SINCE, UNTIL)
        assert result[0]["state"] == "open"
        assert result[0]["draft"] is True
        assert result[0]["closed_at"] is None

    def test_breaks_on_old_prs(self):
        client = _make_client()

        old_pr = MagicMock()
        old_pr.updated_at = datetime(2026, 3, 27, 0, 0, tzinfo=JST)

        repo = MagicMock()
        repo.get_pulls.return_value = [old_pr]

        result = client.fetch_pulls(repo, SINCE, UNTIL)
        assert result == []


class TestFetchIssues:
    def test_excludes_pull_requests(self):
        client = _make_client()

        issue = MagicMock()
        issue.pull_request = None
        issue.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.created_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.closed_at = None
        issue.state_reason = None
        issue.html_url = "https://github.com/n-yU/repo/issues/5"
        issue.number = 5
        issue.title = "Bug report"
        issue.state = "open"
        issue.user.login = "user"
        issue.labels = []

        pr_as_issue = MagicMock()
        pr_as_issue.pull_request = MagicMock()

        repo = MagicMock()
        repo.get_issues.return_value = [issue, pr_as_issue]

        result = client.fetch_issues(repo, SINCE, UNTIL)
        assert len(result) == 1
        assert result[0]["number"] == 5
        assert result[0]["url"] == "https://github.com/n-yU/repo/issues/5"
        assert result[0]["created_at"] == "2026-03-28T10:00:00+09:00"
        assert result[0]["closed_at"] is None

    def test_extracts_labels(self):
        client = _make_client()

        label = MagicMock()
        label.name = "bug"
        issue = MagicMock()
        issue.pull_request = None
        issue.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
        issue.closed_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.state_reason = "completed"
        issue.html_url = "https://github.com/n-yU/repo/issues/6"
        issue.number = 6
        issue.title = "Issue"
        issue.state = "closed"
        issue.user.login = "user"
        issue.labels = [label]

        repo = MagicMock()
        repo.get_issues.return_value = [issue]

        result = client.fetch_issues(repo, SINCE, UNTIL)
        assert result[0]["labels"] == ["bug"]
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"
        assert result[0]["state_reason"] == "completed"


class TestFetchActivity:
    @patch("report.github.time.sleep")
    @patch("report.github.time.time")
    def test_sleeps_remaining_window_time(self, mock_time, mock_sleep):
        """Sleeps only the remaining window time when batch limit is hit."""
        client = _make_client()
        # Simulate: already processed a batch, window started at t=100
        client._search_count = _SEARCH_BATCH
        client._window_start = 100
        # Current time: t=105 → elapsed=5, sleep=15
        mock_time.return_value = 105

        repo_names = ["repo-0"]

        mock_user = MagicMock()
        client.g.get_user.return_value = mock_user

        mock_repo = MagicMock()
        mock_repo.name = "repo"
        mock_repo.full_name = "n-yU/repo"
        mock_user.get_repo.return_value = mock_repo

        client.g.search_commits.return_value = []
        mock_repo.get_pulls.return_value = []
        mock_repo.get_issues.return_value = []

        client.fetch_activity(SINCE, UNTIL, repo_names)

        mock_sleep.assert_called_once_with(_SEARCH_WINDOW - 5)
        assert client._search_count == 1

    @patch("report.github.time.sleep")
    @patch("report.github.time.time")
    def test_skips_sleep_when_window_elapsed(self, mock_time, mock_sleep):
        """Skips sleep when enough time has passed since window start."""
        client = _make_client()
        # Simulate: already processed a batch, window started at t=100
        client._search_count = _SEARCH_BATCH
        client._window_start = 100
        # Current time: t=125 → elapsed=25 > 20s window
        mock_time.return_value = 125

        repo_names = ["repo-0"]

        mock_user = MagicMock()
        client.g.get_user.return_value = mock_user

        mock_repo = MagicMock()
        mock_repo.name = "repo"
        mock_repo.full_name = "n-yU/repo"
        mock_user.get_repo.return_value = mock_repo

        client.g.search_commits.return_value = []
        mock_repo.get_pulls.return_value = []
        mock_repo.get_issues.return_value = []

        client.fetch_activity(SINCE, UNTIL, repo_names)

        mock_sleep.assert_not_called()
        assert client._search_count == 1

    @patch("report.github.time.sleep")
    @patch("report.github.time.time")
    def test_window_starts_on_first_request(self, mock_time, mock_sleep):
        """Window starts when first search request is made, not at init."""
        client = _make_client()
        client._search_count = 0
        client._window_start = 0.0
        mock_time.return_value = 500

        mock_user = MagicMock()
        client.g.get_user.return_value = mock_user

        mock_repo = MagicMock()
        mock_repo.name = "repo"
        mock_repo.full_name = "n-yU/repo"
        mock_user.get_repo.return_value = mock_repo

        client.g.search_commits.return_value = []
        mock_repo.get_pulls.return_value = []
        mock_repo.get_issues.return_value = []

        client.fetch_activity(SINCE, UNTIL, ["repo-0"])

        # Window should be set to current time on first request
        assert client._window_start == 500
        mock_sleep.assert_not_called()


class TestSearchPullsByEvent:
    def test_query_includes_kind_event_and_widened_range(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"
        client.g.search_issues.return_value = []

        client._search_pulls_by_event(repo, SINCE, UNTIL, "merged")

        query = client.g.search_issues.call_args[0][0]
        assert "repo:n-yU/repo" in query
        assert "is:pr" in query
        # Range starts at SINCE - 1day; UNTIL is the literal date
        assert "merged:2026-03-27..2026-03-29" in query

    def test_increments_search_throttle_counter(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"
        client.g.search_issues.return_value = []

        client._search_pulls_by_event(repo, SINCE, UNTIL, "created")
        assert client._search_count == 1


class TestSearchIssuesByEvent:
    def test_query_uses_issue_kind(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"
        client.g.search_issues.return_value = []

        client._search_issues_by_event(repo, SINCE, UNTIL, "closed")

        query = client.g.search_issues.call_args[0][0]
        assert "is:issue" in query
        assert "closed:2026-03-27..2026-03-29" in query


class TestFetchPullsForCommit:
    def test_returns_pr_numbers(self):
        client = _make_client()
        repo = MagicMock()
        commit = MagicMock()
        pr1 = MagicMock()
        pr1.number = 7
        pr2 = MagicMock()
        pr2.number = 12
        commit.get_pulls.return_value = [pr1, pr2]
        repo.get_commit.return_value = commit

        result = client._fetch_pulls_for_commit(repo, "abc1234")

        assert result == [7, 12]
        repo.get_commit.assert_called_once_with("abc1234")

    def test_returns_empty_on_404(self):
        client = _make_client()
        repo = MagicMock()
        repo.get_commit.side_effect = UnknownObjectException(404, "Not Found", {})

        result = client._fetch_pulls_for_commit(repo, "abc1234")

        assert result == []

    def test_propagates_non_404_errors(self):
        client = _make_client()
        repo = MagicMock()
        repo.get_commit.side_effect = RuntimeError("transient failure")

        with pytest.raises(RuntimeError):
            client._fetch_pulls_for_commit(repo, "abc1234")


class TestPopulateCommitPullNumbers:
    def setup_method(self):
        self.client = _make_client()
        self.repo = MagicMock()
        self.client.g.get_user.return_value.get_repo.return_value = self.repo

    def _commit(self, sha, pull_numbers):
        return {
            "sha": sha,
            "message": "m",
            "author": "u",
            "date": "...",
            "url": "...",
            "pull_numbers": pull_numbers,
        }

    def test_skips_commits_with_existing_pull_numbers(self):
        commits = [self._commit("aaa", [3]), self._commit("bbb", [7])]
        self.client.populate_commit_pull_numbers("repo", commits)

        self.repo.get_commit.assert_not_called()
        assert commits[0]["pull_numbers"] == [3]
        assert commits[1]["pull_numbers"] == [7]

    def test_resolves_only_unresolved_commits(self):
        commit_obj = MagicMock()
        commit_obj.sha = "bbb"
        commit_obj.html_url = "https://github.com/n-yU/repo/commit/bbb"
        pr = MagicMock()
        pr.number = 11
        commit_obj.get_pulls.return_value = [pr]
        self.repo.get_commit.return_value = commit_obj

        commits = [self._commit("aaa", [3]), self._commit("bbb", [])]
        self.client.populate_commit_pull_numbers("repo", commits)

        self.repo.get_commit.assert_called_once_with("bbb")
        assert commits[0]["pull_numbers"] == [3]
        assert commits[1]["pull_numbers"] == [11]

    def test_normalizes_short_sha_to_full(self):
        full_sha = "bbb2222abcdef1234abcdef1234abcdef12345678"
        full_url = f"https://github.com/n-yU/repo/commit/{full_sha}"
        commit_obj = MagicMock()
        commit_obj.sha = full_sha
        commit_obj.html_url = full_url
        commit_obj.get_pulls.return_value = []
        self.repo.get_commit.return_value = commit_obj

        commits = [self._commit("bbb2222", [])]
        self.client.populate_commit_pull_numbers("repo", commits)

        self.repo.get_commit.assert_called_once_with("bbb2222")
        assert commits[0]["sha"] == full_sha
        assert commits[0]["url"] == full_url

    def test_assigns_empty_list_on_404(self):
        self.repo.get_commit.side_effect = UnknownObjectException(
            404,
            "Not Found",
            {},
        )

        commits = [self._commit("aaa", [])]
        self.client.populate_commit_pull_numbers("repo", commits)

        assert commits[0]["pull_numbers"] == []

    def test_assigns_empty_list_on_422(self):
        # Short SHA ambiguity / not-found is reported as 422 by
        # GET /commits/{sha}
        self.repo.get_commit.side_effect = GithubException(
            422,
            {"message": "No commit found for SHA: aaa"},
            {},
        )

        commits = [self._commit("aaa", [])]
        self.client.populate_commit_pull_numbers("repo", commits)

        assert commits[0]["pull_numbers"] == []

    def test_propagates_other_github_errors(self):
        self.repo.get_commit.side_effect = GithubException(
            500,
            {"message": "server error"},
            {},
        )

        commits = [self._commit("aaa", [])]
        with pytest.raises(GithubException):
            self.client.populate_commit_pull_numbers("repo", commits)

    def test_skips_api_call_when_no_unresolved(self):
        commits = [self._commit("aaa", [3])]
        self.client.populate_commit_pull_numbers("repo", commits)

        self.client.g.get_user.assert_not_called()


class TestFetchPullsBackfill:
    def test_unions_search_events_and_commit_derived_pulls(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        # Search returns: created→#1, merged→#2, closed→#3
        client.g.search_issues.side_effect = [
            [_make_pr_issue(1)],
            [_make_pr_issue(2)],
            [_make_pr_issue(3)],
        ]

        # Each PR refetch returns a pull whose timestamps are in range
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        repo.get_pull.side_effect = lambda n: _make_pull(
            n,
            created_at=in_range,
        )

        # Commits already carry pull_numbers populated by fetch_commits
        commits = [
            {
                "sha": "deadbee",
                "message": "",
                "author": "",
                "date": in_range.isoformat(),
                "url": "",
                "pull_numbers": [1, 4],
            }
        ]
        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=commits,
        )

        numbers = sorted(r["number"] for r in result)
        assert numbers == [1, 2, 3, 4]
        # Hybrid path no longer calls get_commit (data comes from pull_numbers)
        repo.get_commit.assert_not_called()

    def test_post_filters_search_only_pulls_outside_range(self):
        """Search-derived PRs without an in-range event are dropped."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [
            [_make_pr_issue(10)],  # created
            [],  # merged
            [],  # closed
        ]
        # PR #10 was created on a different day (search widens window by 1 day)
        out_of_range = datetime(2026, 3, 27, 23, 0, tzinfo=JST)
        repo.get_pull.return_value = _make_pull(10, created_at=out_of_range)

        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=[],
        )
        assert result == []

    def test_keeps_commit_derived_pull_without_in_range_event(self):
        """Commit-derived PRs are kept regardless of state-event timing."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], [], []]
        # PR opened weeks ago, no merged/closed yet
        repo.get_pull.return_value = _make_pull(
            99,
            created_at=datetime(2026, 3, 1, 0, 0, tzinfo=JST),
        )

        commits = [
            {
                "sha": "abc",
                "message": "",
                "author": "",
                "date": "",
                "url": "",
                "pull_numbers": [99],
            }
        ]
        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=commits,
        )
        assert [r["number"] for r in result] == [99]

    def test_skips_pull_not_found(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [
            [_make_pr_issue(50)],
            [],
            [],
        ]
        repo.get_pull.side_effect = UnknownObjectException(404, "Not Found", {})

        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=[],
        )
        assert result == []

    def test_propagates_non_404_pull_fetch_errors(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [
            [_make_pr_issue(50)],
            [],
            [],
        ]
        repo.get_pull.side_effect = RuntimeError("transient failure")

        with pytest.raises(RuntimeError):
            client.fetch_pulls(
                repo,
                SINCE,
                UNTIL,
                is_backfill=True,
                commits=[],
            )

    def test_keeps_session_derived_pull_without_in_range_event(self):
        """Session-derived PRs are kept regardless of state-event timing."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], [], []]
        repo.get_pull.return_value = _make_pull(
            77,
            created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST),
        )

        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=[],
            session_numbers=[77],
        )
        assert [r["number"] for r in result] == [77]

    def test_unions_session_with_search_and_dedups(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        # Search→#10 (in range), session→#10, #20
        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        client.g.search_issues.side_effect = [
            [_make_pr_issue(10)],
            [],
            [],
        ]
        repo.get_pull.side_effect = lambda n: _make_pull(
            n,
            created_at=in_range,
        )

        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=[],
            session_numbers=[10, 20],
        )
        assert sorted(r["number"] for r in result) == [10, 20]
        # #10 was fetched once (event ∪ session uses sorted unique numbers)
        called = [c.args[0] for c in repo.get_pull.call_args_list]
        assert called == sorted(called) and len(called) == 2

    def test_skips_session_pull_not_found(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], [], []]
        repo.get_pull.side_effect = UnknownObjectException(404, "Not Found", {})

        result = client.fetch_pulls(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            commits=[],
            session_numbers=[999],
        )
        assert result == []


class TestFetchIssuesBackfill:
    def test_unions_created_and_closed_search(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        i_created = _make_issue(1, created_at=in_range)
        i_closed = _make_issue(
            2,
            created_at=datetime(2026, 3, 1, 0, 0, tzinfo=JST),
            closed_at=in_range,
            state_reason="completed",
        )
        client.g.search_issues.side_effect = [
            [i_created],
            [i_closed],
        ]

        result = client.fetch_issues(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
        )
        assert sorted(r["number"] for r in result) == [1, 2]
        assert result[1]["state_reason"] == "completed"

    def test_post_filters_issue_outside_range(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        # Returned by search but actually outside JST window
        out_of_range = datetime(2026, 3, 27, 22, 0, tzinfo=JST)
        client.g.search_issues.side_effect = [
            [_make_issue(5, created_at=out_of_range)],
            [],
        ]

        result = client.fetch_issues(repo, SINCE, UNTIL, is_backfill=True)
        assert result == []

    def test_keeps_session_derived_issue_without_in_range_event(self):
        """Session-derived issues are kept regardless of state-event timing."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = _make_issue(
            42,
            created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST),
        )

        result = client.fetch_issues(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            session_numbers=[42],
        )
        assert [r["number"] for r in result] == [42]

    def test_skips_session_number_resolving_to_pr(self):
        """Session #N may resolve to a PR; PRs must be filtered out."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], []]
        repo.get_issue.return_value = _make_issue(
            87,
            created_at=datetime(2026, 2, 1, 0, 0, tzinfo=JST),
            pull_request=MagicMock(),
        )

        result = client.fetch_issues(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            session_numbers=[87],
        )
        assert result == []

    def test_skips_session_issue_not_found(self):
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        client.g.search_issues.side_effect = [[], []]
        repo.get_issue.side_effect = UnknownObjectException(404, "Not Found", {})

        result = client.fetch_issues(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            session_numbers=[999],
        )
        assert result == []

    def test_does_not_refetch_session_number_already_in_search(self):
        """Numbers covered by Search are reused; get_issue is only called for new ones."""
        client = _make_client()
        repo = MagicMock()
        repo.full_name = "n-yU/repo"

        in_range = datetime(2026, 3, 28, 12, 0, tzinfo=JST)
        client.g.search_issues.side_effect = [
            [_make_issue(10, created_at=in_range)],
            [],
        ]

        result = client.fetch_issues(
            repo,
            SINCE,
            UNTIL,
            is_backfill=True,
            session_numbers=[10],
        )
        assert [r["number"] for r in result] == [10]
        repo.get_issue.assert_not_called()


class TestFetchActivityBackfill:
    def test_propagates_is_backfill_flag(self):
        client = _make_client()

        mock_user = MagicMock()
        client.g.get_user.return_value = mock_user
        mock_repo = MagicMock()
        mock_repo.name = "repo"
        mock_repo.full_name = "n-yU/repo"
        mock_user.get_repo.return_value = mock_repo

        client.g.search_commits.return_value = []
        # Hybrid path uses search_issues, not get_pulls/get_issues
        client.g.search_issues.return_value = []

        client.fetch_activity(SINCE, UNTIL, ["repo"], is_backfill=True)

        # Default updated_at path must NOT be invoked under backfill
        mock_repo.get_pulls.assert_not_called()
        mock_repo.get_issues.assert_not_called()
        # 5 search calls expected (created/merged/closed PR + created/closed issue)
        assert client.g.search_issues.call_count == 5
