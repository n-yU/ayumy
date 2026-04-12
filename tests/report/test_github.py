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
        repo.full_name = "user/repo"
        client.g.search_commits.return_value = [mock_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "user"
        call_args = client.g.search_commits.call_args
        assert "repo:user/repo" in call_args[0][0]
        assert "author-date:>=" in call_args[0][0]
        assert "author-date:<" in call_args[0][0]
        assert call_args[1] == {"sort": "author-date", "order": "desc"}

    def test_returns_sorted_by_author_date(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        older_commit = MagicMock()
        older_commit.sha = "abc123"
        older_commit.commit.message = "Older commit"
        older_commit.commit.author.name = "user"
        older_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)

        newer_commit = MagicMock()
        newer_commit.sha = "def456"
        newer_commit.commit.message = "Newer commit"
        newer_commit.commit.author.name = "user"
        newer_commit.commit.author.date = datetime(2026, 3, 28, 11, 0, tzinfo=JST)

        repo = MagicMock()
        repo.full_name = "user/repo"
        client.g.search_commits.return_value = [newer_commit, older_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 2
        assert result[0]["sha"] == "def456"
        assert result[1]["sha"] == "abc123"


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
