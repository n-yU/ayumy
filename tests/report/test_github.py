"""Tests for GitHubClient API wrappers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from report import JST
from report.github import GitHubClient, _SEARCH_BATCH, _SEARCH_WINDOW


def _make_client():
    """Create a GitHubClient with mocked PyGithub."""
    client = GitHubClient.__new__(GitHubClient)
    client.g = MagicMock()
    return client


class TestFetchCommits:
    def test_extracts_commit_info(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        mock_commit = MagicMock()
        mock_commit.sha = "abc123"
        mock_commit.commit.message = "Fix bug\n\nDetailed description"
        mock_commit.commit.author.name = "user"
        mock_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        mock_commit.html_url = "https://github.com/n-yU/my-repo/commit/abc123"

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = [mock_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "user"
        assert result[0]["url"] == "https://github.com/n-yU/my-repo/commit/abc123"

    def test_uses_author_date_range_query(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = []

        client.fetch_commits(repo, since, until)

        query = client.g.search_commits.call_args[0][0]
        assert "repo:n-yU/my-repo" in query
        assert "author-date:2026-03-28..2026-03-28" in query

    def test_uses_same_day_range_for_partial_day(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 28, 15, 0, tzinfo=JST)

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = []

        client.fetch_commits(repo, since, until)

        query = client.g.search_commits.call_args[0][0]
        assert "author-date:2026-03-28..2026-03-28" in query

    def test_filters_commits_outside_time_range(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 28, 15, 0, tzinfo=JST)

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

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        assert result[0]["sha"] == "aaa"


class TestFetchPulls:
    def test_determines_merged_state(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

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

        result = client.fetch_pulls(repo, since, until)
        assert len(result) == 1
        assert result[0]["state"] == "merged"
        assert result[0]["draft"] is False
        assert result[0]["url"] == "https://github.com/n-yU/repo/pull/1"
        assert result[0]["created_at"] == "2026-03-27T09:00:00+09:00"
        assert result[0]["merged_at"] == "2026-03-28T10:00:00+09:00"
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"

    def test_determines_closed_state(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

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

        result = client.fetch_pulls(repo, since, until)
        assert result[0]["state"] == "closed"
        assert result[0]["merged_at"] is None
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"

    def test_determines_open_state(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

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

        result = client.fetch_pulls(repo, since, until)
        assert result[0]["state"] == "open"
        assert result[0]["draft"] is True
        assert result[0]["closed_at"] is None

    def test_breaks_on_old_prs(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        old_pr = MagicMock()
        old_pr.updated_at = datetime(2026, 3, 27, 0, 0, tzinfo=JST)

        repo = MagicMock()
        repo.get_pulls.return_value = [old_pr]

        result = client.fetch_pulls(repo, since, until)
        assert result == []


class TestFetchIssues:
    def test_excludes_pull_requests(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        issue = MagicMock()
        issue.pull_request = None
        issue.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.created_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.closed_at = None
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

        result = client.fetch_issues(repo, since, until)
        assert len(result) == 1
        assert result[0]["number"] == 5
        assert result[0]["url"] == "https://github.com/n-yU/repo/issues/5"
        assert result[0]["created_at"] == "2026-03-28T10:00:00+09:00"
        assert result[0]["closed_at"] is None

    def test_extracts_labels(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        label = MagicMock()
        label.name = "bug"
        issue = MagicMock()
        issue.pull_request = None
        issue.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.created_at = datetime(2026, 3, 28, 9, 0, tzinfo=JST)
        issue.closed_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.html_url = "https://github.com/n-yU/repo/issues/6"
        issue.number = 6
        issue.title = "Issue"
        issue.state = "closed"
        issue.user.login = "user"
        issue.labels = [label]

        repo = MagicMock()
        repo.get_issues.return_value = [issue]

        result = client.fetch_issues(repo, since, until)
        assert result[0]["labels"] == ["bug"]
        assert result[0]["closed_at"] == "2026-03-28T10:00:00+09:00"


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
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

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

        client.fetch_activity(since, until, repo_names)

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
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

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

        client.fetch_activity(since, until, repo_names)

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
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        mock_user = MagicMock()
        client.g.get_user.return_value = mock_user

        mock_repo = MagicMock()
        mock_repo.name = "repo"
        mock_repo.full_name = "n-yU/repo"
        mock_user.get_repo.return_value = mock_repo

        client.g.search_commits.return_value = []
        mock_repo.get_pulls.return_value = []
        mock_repo.get_issues.return_value = []

        client.fetch_activity(since, until, ["repo-0"])

        # Window should be set to current time on first request
        assert client._window_start == 500
        mock_sleep.assert_not_called()
