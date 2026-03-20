"""Ayumy daily report generator.

Fetches GitHub activity (commits, pull requests, issues) for the target
date range and formats it for downstream processing.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from github import Github
from github.PaginatedList import PaginatedList
from github.Repository import Repository

JST = timezone(timedelta(hours=9))

# Type aliases for structured activity data
CommitInfo = dict[str, str]
PullInfo = dict[str, Any]
IssueInfo = dict[str, Any]
RepoActivity = dict[str, list[CommitInfo] | list[PullInfo] | list[IssueInfo]]
Activity = dict[str, RepoActivity]


def get_target_date_range(source: str | None = None) -> tuple[datetime, datetime]:
    """Return the target date range for activity fetching.

    Args:
        source: Invocation source. "manual" for manual execution,
            None or other values for scheduled execution

    Returns:
        A tuple of (since, until) as timezone-aware datetime objects.
        Scheduled: previous day JST 00:00 ~ today JST 00:00.
        Manual: today JST 00:00 ~ now
    """
    now_jst = datetime.now(JST)
    today_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    if source == "manual":
        return today_jst, now_jst

    yesterday_jst = today_jst - timedelta(days=1)
    return yesterday_jst, today_jst


class GitHubClient:
    """Client for fetching GitHub activity via PyGithub."""

    def __init__(self, pat: str) -> None:
        """Initialize the client with a GitHub Personal Access Token.

        Args:
            pat: GitHub Fine-grained PAT with read access to owner repos
        """
        self.g = Github(pat)

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
                "sha": c.sha[:7],
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

    def fetch_activity(self, since: datetime, until: datetime) -> Activity:
        """Fetch all GitHub activity for the target date range.

        Args:
            since: Start of the target period (inclusive).
            until: End of the target period (exclusive).

        Returns:
            A dict keyed by repo name. Each value contains "commits",
            "pulls", and "issues" lists. Repos with no activity are omitted
        """
        activity: Activity = {}
        for repo in self.fetch_repos():
            commits = self.fetch_commits(repo, since, until)
            pulls = self.fetch_pulls(repo, since, until)
            issues = self.fetch_issues(repo, since, until)

            if commits or pulls or issues:
                activity[repo.name] = {
                    "commits": commits,
                    "pulls": pulls,
                    "issues": issues,
                }
        return activity


def format_github_activity(activity: Activity) -> str:
    """Format GitHub activity into the text format for Claude API input.

    Args:
        activity: Activity dict as returned by GitHubClient.fetch_activity()

    Returns:
        A Markdown-formatted string following the Spec.md §5.3 input format
    """
    if not activity:
        return "# GitHub アクティビティ\nアクティビティなし"

    lines = ["# GitHub アクティビティ"]
    for repo_name, data in sorted(activity.items()):
        lines.append(f"## {repo_name}")

        if data["commits"]:
            lines.append("### Commits")
            for c in data["commits"]:
                lines.append(f"- {c['message']}")

        if data["pulls"]:
            lines.append("### Pull Requests")
            for pr in data["pulls"]:
                labels = f" ({', '.join(pr['labels'])})" if pr["labels"] else ""
                lines.append(f"- [{pr['state']}] #{pr['number']} {pr['title']}{labels}")

        if data["issues"]:
            lines.append("### Issues")
            for issue in data["issues"]:
                labels = f" ({', '.join(issue['labels'])})" if issue["labels"] else ""
                lines.append(f"- [{issue['state']}] #{issue['number']} {issue['title']}{labels}")

    return "\n".join(lines)


def main() -> None:
    """Entry point for the report generator."""
    github_pat = os.environ.get("GITHUB_PAT")
    if not github_pat:
        print("GITHUB_PAT is not set", file=sys.stderr)
        sys.exit(1)

    source = os.environ.get("AYUMY_SOURCE")
    since, until = get_target_date_range(source)
    print(f"Target date range: {since.isoformat()} ~ {until.isoformat()}")

    client = GitHubClient(github_pat)
    activity = client.fetch_activity(since, until)
    formatted = format_github_activity(activity)
    print(formatted)


if __name__ == "__main__":
    main()
