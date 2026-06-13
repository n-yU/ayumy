"""GitHub activity client."""

import dataclasses
import logging
import time
from datetime import datetime, timedelta
from functools import cached_property

from github import Github, GithubException, UnknownObjectException
from github.Issue import Issue
from github.Repository import Repository

from . import GitHubActivity, RepoActivity
from .domain import CommitInfo, IssueInfo, PullInfo

logger = logging.getLogger(__name__)

# Search API rate limit: 30 requests/minute
_SEARCH_BATCH = 10
_SEARCH_WINDOW = 20

_PULL_EVENTS = ("created", "merged", "closed")
_ISSUE_EVENTS = ("created", "closed")


class GitHubClient:
    """Client for fetching and formatting GitHub activity via PyGithub."""

    def __init__(self, pat: str) -> None:
        self.g = Github(pat, per_page=100)
        self._search_count = 0
        self._window_start = 0.0

    @cached_property
    def owner(self) -> str:
        """Login name of the authenticated user (owner of accessible repos)."""
        return self.g.get_user().login

    def _search_throttle(self) -> None:
        """Throttle Search API calls to stay within the secondary rate limit.

        Call this once before each Search API request;
        once the per-window counter reaches the batch size,
        sleep until the window elapses and then reset the counter.
        """
        if self._search_count >= _SEARCH_BATCH:
            elapsed = time.time() - self._window_start
            if elapsed < _SEARCH_WINDOW:
                sleep_time = _SEARCH_WINDOW - elapsed
                logger.info("Search API throttle: sleeping %.0fs", sleep_time)
                time.sleep(sleep_time)
            self._search_count = 0
        if self._search_count == 0:
            self._window_start = time.time()
        self._search_count += 1

    def fetch_commits(
        self, repo: Repository, since: datetime, until: datetime
    ) -> list[CommitInfo]:
        """Fetch commits for `repo` within `[since, until)` and annotate each with associated PR numbers.

        Uses the Search Commits API (author-date range) to cover all branches;
        the API is date-granular, so results are re-filtered against the exact `since` / `until` timestamps.
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
        """Fetch pull requests for `repo` within `[since, until)`.

        The default path filters by `updated_at`.
        The backfill path (`is_backfill=True`) unions Search-by-event results with PRs derived from `commits` in range and from `session_numbers`,
        which recovers PRs whose `updated_at` has since drifted out of the window (Spec.md §5.1.1).
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
        """Fetch issues (excluding PRs) for `repo` within `[since, until)`.

        The default path filters by `updated_at`.
        The backfill path (`is_backfill=True`) unions Search-by-event results with `session_numbers` to recover issues whose `updated_at` has drifted out of the window (Spec.md §5.1.1).
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
        """Fetch commits, PRs, and issues for each repo in `repo_names`, omitting repos that produced no activity."""
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
        """Search PRs whose `event` (created / merged / closed) timestamp lies within `[since, until)`.

        `search_issues` returns Issue objects even for PR queries;
        callers fetch the full PR fields via `repo.get_pull(number)` when needed.
        """
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
        """Search issues (excluding PRs) by state-transition event."""
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
        """Build a Search Issues query string.

        The query covers `since - 1day` through `until` to absorb
        UTC/JST boundary skew. Callers must filter results against
        the exact since/until timestamps
        """
        since_str = (since - timedelta(days=1)).strftime("%Y-%m-%d")
        until_str = until.strftime("%Y-%m-%d")
        return f"repo:{repo.full_name} is:{kind} {event}:{since_str}..{until_str}"

    def _fetch_pulls_for_commit(
        self,
        repo: Repository,
        sha: str,
    ) -> list[int]:
        """Resolve PR numbers associated with a commit SHA.

        Returns an empty list when the commit is not found (404) or
        has no associated PR. Other API errors propagate
        """
        try:
            commit = repo.get_commit(sha)
            return [pr.number for pr in commit.get_pulls()]
        except UnknownObjectException:
            logger.warning("Commit %s not found (404), skipping", sha[:7])
            return []

    def populate_commit_pull_numbers(
        self,
        repo_name: str,
        commits: list[CommitInfo],
    ) -> None:
        """Fill pull_numbers and normalize SHA for the given commits.

        Cross-repo commits are filtered upstream by session ingest
        (cwd-based gate in store.py), so this method assumes every
        commit belongs to repo_name. 404 and 422 are tolerated as
        safety nets for force-deleted SHAs and ambiguous short SHAs
        respectively so a single missing commit does not abort the
        whole report.
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
                logger.warning(
                    "Commit %s lookup failed (%s); keeping commit but "
                    "skipping PR association",
                    c.short_sha,
                    e.status,
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
        """Fetch PRs via Search events + commit + session union (backfill).

        - Commit-derived numbers are exempt from the date-range filter
          (a commit on the target day proves activity)
        - Session-derived numbers are exempt (session touched the PR)
        - Reuses pull_numbers populated by fetch_commits to avoid duplicate API calls
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
                logger.warning("PR #%d not found (404), skipping", n)
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
        """Fetch issues via Search events + session-derived union (backfill).

        Session-derived numbers may resolve to PRs (PR/Issue numbering
        is shared); fetch them via `get_issue` and skip when the
        `pull_request` attribute is set
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
