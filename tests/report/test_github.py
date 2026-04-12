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

        branch = MagicMock()
        branch.name = "main"
        branch.commit.sha = "head111"

        repo = MagicMock()
        repo.get_branches.return_value = [branch]
        repo.get_commits.return_value = [mock_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "user"
        repo.get_commits.assert_called_once_with(sha="main", since=since, until=until)

    def test_deduplicates_across_branches(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        shared_commit = MagicMock()
        shared_commit.sha = "abc123"
        shared_commit.commit.message = "Shared commit"
        shared_commit.commit.author.name = "user"
        shared_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)

        feature_commit = MagicMock()
        feature_commit.sha = "def456"
        feature_commit.commit.message = "Feature work"
        feature_commit.commit.author.name = "user"
        feature_commit.commit.author.date = datetime(2026, 3, 28, 11, 0, tzinfo=JST)

        main_branch = MagicMock()
        main_branch.name = "main"
        main_branch.commit.sha = "head111"
        feature_branch = MagicMock()
        feature_branch.name = "feature/x"
        feature_branch.commit.sha = "head222"

        repo = MagicMock()
        repo.get_branches.return_value = [main_branch, feature_branch]
        repo.get_commits.side_effect = [
            [shared_commit],
            [shared_commit, feature_commit],
        ]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 2
        assert {r["sha"] for r in result} == {"abc123", "def456"}

    def test_skips_branches_with_same_head_sha(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        mock_commit = MagicMock()
        mock_commit.sha = "abc123"
        mock_commit.commit.message = "Fix bug"
        mock_commit.commit.author.name = "user"
        mock_commit.commit.author.date = datetime(2026, 3, 28, 10, 0, tzinfo=JST)

        branch_a = MagicMock()
        branch_a.name = "main"
        branch_a.commit.sha = "head111"
        branch_b = MagicMock()
        branch_b.name = "alias"
        branch_b.commit.sha = "head111"

        repo = MagicMock()
        repo.get_branches.return_value = [branch_a, branch_b]
        repo.get_commits.return_value = [mock_commit]

        result = client.fetch_commits(repo, since, until)
        assert len(result) == 1
        repo.get_commits.assert_called_once_with(sha="main", since=since, until=until)


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
