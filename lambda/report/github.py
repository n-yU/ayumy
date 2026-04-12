"""GitHub activity client."""

from datetime import datetime

from github import Github
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
        seen: set[str] = set()
        seen_branch_heads: set[str] = set()
        results: list[CommitInfo] = []

        for branch in repo.get_branches():
            head_sha = branch.commit.sha
            if head_sha in seen_branch_heads:
                continue
            seen_branch_heads.add(head_sha)
            for c in repo.get_commits(sha=branch.name, since=since, until=until):
                if c.sha in seen:
                    continue
                seen.add(c.sha)
                results.append({
                    "sha": c.sha,
                    "message": c.commit.message.split("\n")[0],
                    "author": c.commit.author.name,
                    "date": c.commit.author.date.isoformat(),
                })

        results.sort(
            key=lambda c: (datetime.fromisoformat(c["date"]), c["sha"]),
            reverse=True,
        )
        return results

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

    def fetch_activity(
        self, since: datetime, until: datetime, repo_names: list[str],
    ) -> GitHubActivity:
        """Fetch GitHub activity for the specified repositories.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)
            repo_names: Repository names to fetch activity for

        Returns:
            A GitHubActivity instance. Repos with no activity are omitted
        """
        user = self.g.get_user()
        data: dict[str, RepoActivity] = {}

        for name in repo_names:
            repo = user.get_repo(name)
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
