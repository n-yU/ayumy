"""GitHub activity client."""

import logging
import time
from datetime import datetime, timedelta
from functools import cached_property

from github import Github
from github.Repository import Repository

from . import CommitInfo, GitHubActivity, IssueInfo, PullInfo, RepoActivity

logger = logging.getLogger(__name__)

# Search API rate limit: 30 requests/minute
_SEARCH_BATCH = 10
_SEARCH_WINDOW = 20


class GitHubClient:
    """Client for fetching and formatting GitHub activity via PyGithub."""

    def __init__(self, pat: str) -> None:
        """Initialize the client with a GitHub Personal Access Token.

        Args:
            pat: GitHub Fine-grained PAT with read access to owner repos
        """
        self.g = Github(pat, per_page=100)
        self._search_count = 0
        self._window_start = 0.0

    @cached_property
    def owner(self) -> str:
        """Login name of the authenticated user (owner of accessible repos)."""
        return self.g.get_user().login

    def fetch_commits(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[CommitInfo]:
        """Fetch commits for a repo within the target date range.

        Uses the Search Commits API with author-date range to find
        commits regardless of branch existence. The Search API only
        supports date-level granularity, so results are post-filtered
        against the exact since/until timestamps.

        Args:
            repo: Target repository
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A list of dicts with keys: sha, message, author, date, url
        """
        since_str = since.strftime("%Y-%m-%d")
        until_date = until - timedelta(days=1)
        until_str = max(since_str, until_date.strftime("%Y-%m-%d"))
        query = f"repo:{repo.full_name} author-date:{since_str}..{until_str}"

        results: list[CommitInfo] = []
        for c in self.g.search_commits(query, sort="author-date", order="desc"):
            author_date = c.commit.author.date
            if author_date < since or author_date >= until:
                continue
            results.append({
                "sha": c.sha,
                "message": c.commit.message.split("\n")[0],
                "author": c.commit.author.name,
                "date": author_date.isoformat(),
                "url": c.html_url,
            })
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
            A list of dicts with keys: number, title, state, author, labels,
            draft, url, created_at, merged_at, closed_at. State is one of
            "merged", "closed", "open"
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
                "draft": bool(pr.draft),
                "url": pr.html_url,
                "created_at": pr.created_at.isoformat(),
                "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
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
            A list of dicts with keys: number, title, state, author, labels,
            url, created_at, closed_at, state_reason
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
                "url": issue.html_url,
                "created_at": issue.created_at.isoformat(),
                "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
                "state_reason": issue.state_reason,
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
            if self._search_count >= _SEARCH_BATCH:
                elapsed = time.time() - self._window_start
                if elapsed < _SEARCH_WINDOW:
                    sleep_time = _SEARCH_WINDOW - elapsed
                    logger.info("Search API throttle: sleeping %.0fs", sleep_time)
                    time.sleep(sleep_time)
                self._search_count = 0
            commits = self.fetch_commits(repo, since, until)
            if self._search_count == 0:
                self._window_start = time.time()
            self._search_count += 1
            pulls = self.fetch_pulls(repo, since, until)
            issues = self.fetch_issues(repo, since, until)

            if commits or pulls or issues:
                data[repo.name] = {
                    "commits": commits,
                    "pulls": pulls,
                    "issues": issues,
                }
        return GitHubActivity(data)
