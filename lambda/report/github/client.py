"""GitHub activity client."""

import dataclasses
import logging
import time
from datetime import datetime, timedelta
from functools import cached_property

from github import Github, GithubException, UnknownObjectException
from github.Issue import Issue
from github.Repository import Repository

from config import CONFIG

from ..domain.activity import (
    CommitInfo,
    GitHubActivity,
    IssueInfo,
    PullInfo,
    RepoActivity,
)
from ..shared.notice import Notice, NoticeSource

logger = logging.getLogger(__name__)

_PULL_EVENTS = ("created", "merged", "closed")
_ISSUE_EVENTS = ("created", "closed")


class GitHubClient:
    """GitHub activity fetcher via PyGithub."""

    def __init__(self, pat: str, notice: Notice | None = None) -> None:
        self.g = Github(pat, per_page=100)
        self._search_count = 0
        self._window_start = 0.0
        self._notice = notice or Notice()

    @cached_property
    def owner(self) -> str:
        return self.g.get_user().login

    def _search_throttle(self) -> None:
        """Call this once before each Search API request; sleeps when the per-window count reaches the batch size."""
        if self._search_count >= CONFIG.github.search_batch:
            elapsed = time.time() - self._window_start
            if elapsed < CONFIG.github.search_window_sec:
                sleep_time = CONFIG.github.search_window_sec - elapsed
                logger.info("Search API throttle: sleeping %.0fs", sleep_time)
                time.sleep(sleep_time)
            self._search_count = 0
        if self._search_count == 0:
            self._window_start = time.time()
        self._search_count += 1

    def fetch_commits(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[CommitInfo]:
        """Annotate each commit with PR numbers.

        Uses Search Commits API (author-date) to cover all branches;
        results re-filtered against exact timestamps.
        """
        # Widen by 1 day on each side to absorb GitHub Search's UTC date semantics,
        # since a JST day spans two UTC dates;
        # precise filtering happens below via the timezone-aware datetime compare
        since_str = (since - timedelta(days=1)).strftime("%Y-%m-%d")
        until_str = until.strftime("%Y-%m-%d")
        query = f"repo:{repo.full_name} author-date:{since_str}..{until_str}"

        self._search_throttle()
        results: list[CommitInfo] = []
        for c in self.g.search_commits(query, sort="author-date", order="desc"):
            author_date = c.commit.author.date
            if author_date < since or author_date >= until:
                continue
            results.append(
                CommitInfo.from_search_commit(
                    c, pull_numbers=self._fetch_pulls_for_commit(repo, c.sha)
                )
            )
        return results

    def fetch_pulls(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        *,
        is_backfill: bool = False,
        commits: list[CommitInfo] | None = None,
        session_numbers: list[int] | None = None,
    ) -> list[PullInfo]:
        """Fetch PRs in `repo` active within the window.

        Default path filters by `updated_at`.
        Backfill path (Spec: Hybrid Backfill Fetch) unions Search-by-event with PRs from `commits` and `session_numbers` to recover PRs whose `updated_at` has drifted out.
        """
        if is_backfill:
            return self._fetch_pulls_hybrid(
                repo,
                since,
                until,
                commits or [],
                session_numbers or [],
            )

        results: list[PullInfo] = []
        for pr in repo.get_pulls(state="all", sort="updated", direction="desc"):
            if pr.updated_at < since:
                break
            if pr.updated_at >= until:
                continue
            results.append(PullInfo.from_pull_request(pr))
        return results

    def fetch_issues(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        *,
        is_backfill: bool = False,
        session_numbers: list[int] | None = None,
    ) -> list[IssueInfo]:
        """Fetch issues in `repo` active within the window.

        Default path filters by `updated_at`.
        Backfill path (Spec: Hybrid Backfill Fetch) unions Search-by-event with `session_numbers` to recover issues whose `updated_at` has drifted out.
        """
        if is_backfill:
            return self._fetch_issues_hybrid(
                repo,
                since,
                until,
                session_numbers or [],
            )

        results: list[IssueInfo] = []
        for issue in repo.get_issues(since=since, state="all"):
            if issue.pull_request is not None:
                continue
            if issue.updated_at >= until:
                continue
            results.append(IssueInfo.from_issue(issue))
        return results

    def fetch_activity(
        self,
        since: datetime,
        until: datetime,
        repo_names: list[str],
        *,
        is_backfill: bool = False,
        session_pulls: dict[str, list[int]] | None = None,
        session_issues: dict[str, list[int]] | None = None,
    ) -> GitHubActivity:
        user = self.g.get_user()
        data: dict[str, RepoActivity] = {}
        session_pulls = session_pulls or {}
        session_issues = session_issues or {}

        for name in repo_names:
            repo = user.get_repo(name)
            commits = self.fetch_commits(repo, since, until)
            pulls = self.fetch_pulls(
                repo,
                since,
                until,
                is_backfill=is_backfill,
                commits=commits,
                session_numbers=session_pulls.get(name),
            )
            issues = self.fetch_issues(
                repo,
                since,
                until,
                is_backfill=is_backfill,
                session_numbers=session_issues.get(name),
            )

            if commits or pulls or issues:
                data[repo.name] = {
                    "commits": commits,
                    "pulls": pulls,
                    "issues": issues,
                }
        return GitHubActivity(data)

    def _search_pulls_by_event(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        event: str,
    ) -> list[Issue]:
        """Search PRs by state `event`; `search_issues` returns Issue objects even for PR queries, so callers fetch full PR fields via `repo.get_pull(number)` when needed."""
        query = self._build_search_query(repo, since, until, "pr", event)
        self._search_throttle()
        return list(self.g.search_issues(query))

    def _search_issues_by_event(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        event: str,
    ) -> list[Issue]:
        query = self._build_search_query(repo, since, until, "issue", event)
        self._search_throttle()
        return list(self.g.search_issues(query))

    @staticmethod
    def _build_search_query(
        repo: Repository,
        since: datetime,
        until: datetime,
        kind: str,
        event: str,
    ) -> str:
        """Widens range by 1 day to absorb UTC/JST boundary skew; callers must filter results against exact `since` / `until`."""
        since_str = (since - timedelta(days=1)).strftime("%Y-%m-%d")
        until_str = until.strftime("%Y-%m-%d")
        return f"repo:{repo.full_name} is:{kind} {event}:{since_str}..{until_str}"

    def _fetch_pulls_for_commit(
        self,
        repo: Repository,
        sha: str,
    ) -> list[int]:
        """Return the numbers of PRs associated with `sha`, empty on 404; other API errors propagate."""
        try:
            commit = repo.get_commit(sha)
            return [pr.number for pr in commit.get_pulls()]
        except UnknownObjectException:
            self._notice.add(
                NoticeSource.GITHUB,
                "Commit not found (404)",
                logger=logger,
                repo=repo.full_name,
                sha=sha[:7],
            )
            return []

    def populate_commit_pull_numbers(
        self,
        repo_name: str,
        commits: list[CommitInfo],
    ) -> None:
        """Resolve associated PRs for commits lacking them, replacing each with its full SHA and URL from the API response.

        Assumes commits all belong to `repo_name` (cross-repo filtered upstream in store.py).
        404/422 are tolerated as safety nets for force-deleted or ambiguous SHAs.
        """
        unresolved_indices = [i for i, c in enumerate(commits) if not c.pull_numbers]
        if not unresolved_indices:
            return
        repo = self.g.get_user().get_repo(repo_name)
        for i in unresolved_indices:
            c = commits[i]
            try:
                commit = repo.get_commit(c.sha)
            except GithubException as e:
                if e.status not in (404, 422):
                    raise
                self._notice.add(
                    NoticeSource.GITHUB,
                    "Commit PR lookup failed",
                    logger=logger,
                    repo=repo_name,
                    sha=c.short_sha,
                    status=str(e.status),
                )
                commits[i] = c.with_pull_numbers(())
                continue
            commits[i] = dataclasses.replace(
                c,
                sha=commit.sha,
                url=commit.html_url,
                pull_numbers=tuple(pr.number for pr in commit.get_pulls()),
            )

    def _fetch_pulls_hybrid(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        commits: list[CommitInfo],
        session_numbers: list[int],
    ) -> list[PullInfo]:
        """Union Search-by-event PRs with those derived from commits and sessions.

        Commit/session-derived numbers bypass the date filter (activity proven by commit/session touch); reuses pull_numbers populated by `fetch_commits` to avoid duplicate API calls.
        """
        event_numbers: set[int] = set()
        for event in _PULL_EVENTS:
            for item in self._search_pulls_by_event(repo, since, until, event):
                event_numbers.add(item.number)

        commit_numbers: set[int] = set()
        for c in commits:
            commit_numbers.update(c.pull_numbers)

        session_set: set[int] = set(session_numbers)
        exempt_numbers = commit_numbers | session_set

        results: list[PullInfo] = []
        for n in sorted(event_numbers | exempt_numbers):
            try:
                pr = repo.get_pull(n)
            except UnknownObjectException:
                self._notice.add(
                    NoticeSource.GITHUB,
                    "PR not found (404)",
                    logger=logger,
                    repo=repo.full_name,
                    number=str(n),
                )
                continue
            info = PullInfo.from_pull_request(pr)
            # Search-only entries must have a state event in [since, until)
            if n in event_numbers and n not in exempt_numbers:
                if not info.has_event_in_range(since, until):
                    continue
            results.append(info)
        return results

    def _fetch_issues_hybrid(
        self,
        repo: Repository,
        since: datetime,
        until: datetime,
        session_numbers: list[int],
    ) -> list[IssueInfo]:
        """Union Search-by-event issues with those derived from sessions.

        Session-derived numbers may resolve to PRs (PR/Issue numbering is shared); fetched via `get_issue` and skipped when `pull_request` is set.
        """
        seen: dict[int, Issue] = {}
        for event in _ISSUE_EVENTS:
            for item in self._search_issues_by_event(repo, since, until, event):
                seen.setdefault(item.number, item)
        event_numbers = set(seen)

        session_set: set[int] = set(session_numbers)
        for number in session_set - event_numbers:
            try:
                issue = repo.get_issue(number)
            except UnknownObjectException:
                logger.warning("Issue #%d not found (404), skipping", number)
                continue
            if issue.pull_request is not None:
                continue
            seen[number] = issue

        results: list[IssueInfo] = []
        for number in sorted(seen):
            issue = seen[number]
            info = IssueInfo.from_issue(issue)
            # Search-only entries must have a state event in [since, until)
            if number in event_numbers and number not in session_set:
                if not info.has_event_in_range(since, until):
                    continue
            results.append(info)
        return results
