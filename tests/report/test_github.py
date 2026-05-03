"""Tests for GitHubClient API wrappers."""

from datetime import datetime
from unittest.mock import MagicMock

from report import JST
from report.github import GitHubClient


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

        repo = MagicMock()
        repo.full_name = "n-yU/my-repo"
        client.g.search_commits.return_value = [mock_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "user"

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
        pr.number = 1
        pr.title = "Add feature"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, since, until)
        assert len(result) == 1
        assert result[0]["state"] == "merged"

    def test_determines_closed_state(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        pr = MagicMock()
        pr.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.merged_at = None
        pr.state = "closed"
        pr.number = 2
        pr.title = "Rejected PR"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, since, until)
        assert result[0]["state"] == "closed"

    def test_determines_open_state(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        pr = MagicMock()
        pr.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        pr.merged_at = None
        pr.state = "open"
        pr.number = 3
        pr.title = "WIP"
        pr.user.login = "user"
        pr.labels = []

        repo = MagicMock()
        repo.get_pulls.return_value = [pr]

        result = client.fetch_pulls(repo, since, until)
        assert result[0]["state"] == "open"

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

    def test_extracts_labels(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        label = MagicMock()
        label.name = "bug"
        issue = MagicMock()
        issue.pull_request = None
        issue.updated_at = datetime(2026, 3, 28, 10, 0, tzinfo=JST)
        issue.number = 6
        issue.title = "Issue"
        issue.state = "closed"
        issue.user.login = "user"
        issue.labels = [label]

        repo = MagicMock()
        repo.get_issues.return_value = [issue]

        result = client.fetch_issues(repo, since, until)
        assert result[0]["labels"] == ["bug"]
