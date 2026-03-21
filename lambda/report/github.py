"""GitHub activity client."""

from datetime import datetime

from github import Github
from github.PaginatedList import PaginatedList
from github.Repository import Repository

from . import CommitInfo, GitHubActivity, IssueInfo, PullInfo, RepoActivity


class GitHubClient:
    """Client for fetching and formatting GitHub activity via PyGithub."""

    def __init__(self, pat: str) -> None:
        """Initialize the client with a GitHub Personal Access Token.

        Args:
            pat: GitHub Fine-grained PAT with read access to owner repos
        """
        self.g = Github(pat, per_page=100)

    def fetch_repos(self) -> PaginatedList[Repository]:
        """Fetch all owner-affiliated repositories.

        Returns:
            A paginated list of Repository objects owned by the
            authenticated user
        """
        return self.g.get_user().get_repos(affiliation="owner")

    def fetch_commits(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[CommitInfo]:
        """Fetch commits for a repo within the target date range.

        Args:
            repo: Target repository
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A list of dicts with keys: sha, message, author, date
        """
        return [
            {
                "sha": c.sha,
                "message": c.commit.message.split("\n")[0],
                "author": c.commit.author.name,
                "date": c.commit.author.date.isoformat(),
            }
            for c in repo.get_commits(since=since, until=until)
        ]

    def fetch_pulls(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[PullInfo]:
        """Fetch pull requests updated within the target date range.

        Args:
            repo: Target repository
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A list of dicts with keys: number, title, state, author, labels.
            State is one of "merged", "closed", "open"
        """
        results: list[PullInfo] = []
        for pr in repo.get_pulls(state="all", sort="updated", direction="desc"):
            if pr.updated_at < since:
                break
            if pr.updated_at >= until:
                continue

            if pr.merged_at:
                state = "merged"
            elif pr.state == "closed":
                state = "closed"
            else:
                state = "open"

            results.append({
                "number": pr.number,
                "title": pr.title,
                "state": state,
                "author": pr.user.login,
                "labels": [l.name for l in pr.labels],
            })
        return results

    def fetch_issues(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[IssueInfo]:
        """Fetch issues (excluding PRs) updated within the target date range.

        Args:
            repo: Target repository
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A list of dicts with keys: number, title, state, author, labels
        """
        results: list[IssueInfo] = []
        for issue in repo.get_issues(since=since, state="all"):
            if issue.pull_request is not None:
                continue
            if issue.updated_at >= until:
                continue
            results.append({
                "number": issue.number,
                "title": issue.title,
                "state": issue.state,
                "author": issue.user.login,
                "labels": [l.name for l in issue.labels],
            })
        return results

    def fetch_activity(self, since: datetime, until: datetime) -> GitHubActivity:
        """Fetch all GitHub activity for the target date range.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A GitHubActivity instance. Repos with no activity are omitted
        """
        data: dict[str, RepoActivity] = {}
        for repo in self.fetch_repos():
            commits = self.fetch_commits(repo, since, until)
            pulls = self.fetch_pulls(repo, since, until)
            issues = self.fetch_issues(repo, since, until)

            if commits or pulls or issues:
                data[repo.name] = {
                    "commits": commits,
                    "pulls": pulls,
                    "issues": issues,
                }
        return GitHubActivity(data)
