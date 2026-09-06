"""Frozen dataclasses for PR / Issue / Commit with their domain behavior, the repo-keyed container over them, and the shared target-date window predicate."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, KeysView

    from github.Commit import Commit
    from github.Issue import Issue
    from github.PullRequest import PullRequest

    from .session import SessionInfo


_IRREGULAR_ISSUE_REASONS = {"not_planned": "not planned", "duplicate": "duplicate"}


def in_range(iso_timestamp: str | None, since: datetime, until: datetime) -> bool:
    if not iso_timestamp:
        return False
    return since <= datetime.fromisoformat(iso_timestamp) < until


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Commit in a repository with the PRs it is associated with."""

    sha: str
    message: str
    author: str
    date: str
    url: str
    pull_numbers: tuple[int, ...] = ()

    SHA_PREFIX_LEN: ClassVar[int] = 7

    @property
    def short_sha(self) -> str:
        return self.sha[: self.SHA_PREFIX_LEN]

    def label(self) -> str:
        """Return the short SHA and message used in status / timeline rows."""
        return f"{self.short_sha}: {self.message}"

    def is_in_range(self, since: datetime, until: datetime) -> bool:
        return in_range(self.date, since, until)

    @classmethod
    def from_search_commit(
        cls,
        commit: Commit,
        pull_numbers: Iterable[int] = (),
    ) -> CommitInfo:
        """Build a CommitInfo from a Search API commit; `pull_numbers` is supplied separately because resolving associated PRs requires an extra API call."""
        return cls(
            sha=commit.sha,
            message=commit.commit.message.split("\n")[0],
            author=commit.commit.author.name,
            date=commit.commit.author.date.isoformat(),
            url=commit.html_url,
            pull_numbers=tuple(pull_numbers),
        )

    def with_pull_numbers(self, pull_numbers: Iterable[int]) -> CommitInfo:
        return dataclasses.replace(self, pull_numbers=tuple(pull_numbers))


@dataclass(frozen=True, slots=True)
class PullInfo:
    """Pull request with its lifecycle timestamps and labels."""

    number: int
    title: str
    state: str
    author: str
    labels: tuple[str, ...]
    draft: bool
    url: str
    created_at: str
    merged_at: str | None
    closed_at: str | None
    merge_commit_sha: str | None

    def label(self) -> str:
        """Return the PR number and title used in status / timeline rows."""
        return f"#{self.number}: {self.title}"

    def done_prefix(self) -> str:
        """`closed` (unmerged) PRs are flagged as irregular."""
        if self.state == "merged":
            return "✅ "
        return "⚠️ (closed) "

    def has_event_in_range(self, since: datetime, until: datetime) -> bool:
        return any(
            in_range(ts, since, until)
            for ts in (self.created_at, self.merged_at, self.closed_at)
        )

    @property
    def completed_at(self) -> str | None:
        return self.merged_at or self.closed_at

    def state_in_range(self, since: datetime, until: datetime) -> str | None:
        """State as of the end of the window, or None once the PR had already completed before `since`.

        A PR completed after the window is reported as `open` because the completion belongs to the day it happened.
        """
        completed = self.completed_at
        if completed is None or datetime.fromisoformat(completed) >= until:
            return "open"
        return self.state if in_range(completed, since, until) else None

    @classmethod
    def from_pull_request(cls, pr: PullRequest) -> PullInfo:
        if pr.merged_at:
            state = "merged"
        elif pr.state == "closed":
            state = "closed"
        else:
            state = "open"
        return cls(
            number=pr.number,
            title=pr.title,
            state=state,
            author=pr.user.login,
            labels=tuple(label.name for label in pr.labels),
            draft=bool(pr.draft),
            url=pr.html_url,
            created_at=pr.created_at.isoformat(),
            merged_at=pr.merged_at.isoformat() if pr.merged_at else None,
            closed_at=pr.closed_at.isoformat() if pr.closed_at else None,
            # Skip GitHub's "test merge" SHA returned for unmerged PRs
            merge_commit_sha=pr.merge_commit_sha if pr.merged_at else None,
        )


@dataclass(frozen=True, slots=True)
class IssueInfo:
    """Issue with its lifecycle timestamps and labels."""

    number: int
    title: str
    state: str
    author: str
    labels: tuple[str, ...]
    url: str
    created_at: str
    closed_at: str | None
    state_reason: str | None

    def label(self) -> str:
        """Return the issue number and title used in status / timeline rows."""
        return f"#{self.number}: {self.title}"

    def done_prefix(self) -> str:
        """`not_planned` / `duplicate` are flagged; legacy `state_reason=None` is regular Done."""
        label = _IRREGULAR_ISSUE_REASONS.get(self.state_reason or "")
        if label is None:
            return "✅ "
        return f"⚠️ ({label}) "

    def timeline_close_prefix(self) -> str:
        label = _IRREGULAR_ISSUE_REASONS.get(self.state_reason or "")
        if label is None:
            return "✅ close: "
        return f"⚠️ close ({label}): "

    def has_event_in_range(self, since: datetime, until: datetime) -> bool:
        return any(
            in_range(ts, since, until) for ts in (self.created_at, self.closed_at)
        )

    @property
    def completed_at(self) -> str | None:
        return self.closed_at

    def state_in_range(self, since: datetime, until: datetime) -> str | None:
        """State as of the end of the window, or None once the issue had already closed before `since`.

        An issue closed after the window is reported as `open` because the close belongs to the day it happened.
        """
        completed = self.completed_at
        if completed is None or datetime.fromisoformat(completed) >= until:
            return "open"
        return self.state if in_range(completed, since, until) else None

    @classmethod
    def from_issue(cls, issue: Issue) -> IssueInfo:
        return cls(
            number=issue.number,
            title=issue.title,
            state=issue.state,
            author=issue.user.login,
            labels=tuple(label.name for label in issue.labels),
            url=issue.html_url,
            created_at=issue.created_at.isoformat(),
            closed_at=issue.closed_at.isoformat() if issue.closed_at else None,
            state_reason=issue.state_reason,
        )


class RepoActivity(TypedDict):
    commits: list[CommitInfo]
    pulls: list[PullInfo]
    issues: list[IssueInfo]


class GitHubActivity:
    """GitHub activity data keyed by repository name."""

    def __init__(self, data: dict[str, RepoActivity]) -> None:
        self._data = data

    def repos(self) -> dict[str, RepoActivity]:
        return self._data

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def merge_session_commits(
        self,
        repo_name: str,
        sessions: list[SessionInfo],
        *,
        owner: str,
        populate_pull_numbers: Callable[[str, list[CommitInfo]], None],
    ) -> None:
        """Dedup by SHA across `sessions`, drop those already covered by GitHub search (SHA-prefix match), and resolve PR association via `populate_pull_numbers` so injected commits nest under their parent PR."""
        seen_shas: set[str] = set()
        session_commits: list[CommitInfo] = []
        for s in sessions:
            for c in s["session_commits"]:
                if c["sha"] in seen_shas:
                    continue
                seen_shas.add(c["sha"])
                session_commits.append(
                    CommitInfo(
                        sha=c["sha"],
                        message=c["message"],
                        author="",
                        date=c.get("timestamp") or s["start_time"],
                        url=f"https://github.com/{owner}/{repo_name}/commit/{c['sha']}",
                    )
                )
        if not session_commits:
            return
        repo_data = self._data.get(repo_name)
        if repo_data is not None:
            existing_shas = {c.sha for c in repo_data["commits"]}
            new_commits = [
                sc
                for sc in session_commits
                if not any(
                    existing_sha.startswith(sc.sha) for existing_sha in existing_shas
                )
            ]
            if new_commits:
                populate_pull_numbers(repo_name, new_commits)
                repo_data["commits"].extend(new_commits)
        else:
            populate_pull_numbers(repo_name, session_commits)
            self._data[repo_name] = {
                "commits": session_commits,
                "pulls": [],
                "issues": [],
            }

    def format(self, since: datetime, until: datetime) -> str:
        """Format GitHub activity as the Markdown block consumed by the Claude API prompt (Spec: Summary Generation).

        Filtered on the same window basis as the report page so the summary cannot describe items the page does not list.
        """
        lines = ["# GitHub アクティビティ"]
        for repo_name, data in sorted(self._data.items()):
            repo_lines: list[str] = []

            commits = [c for c in data["commits"] if c.is_in_range(since, until)]
            if commits:
                repo_lines.append("### Commits")
                for c in commits:
                    repo_lines.append(f"- {c.message}")

            pulls = [
                (pr, state)
                for pr in data["pulls"]
                if (state := pr.state_in_range(since, until)) is not None
            ]
            if pulls:
                repo_lines.append("### Pull Requests")
                for pr, state in pulls:
                    labels = f" ({', '.join(pr.labels)})" if pr.labels else ""
                    repo_lines.append(f"- [{state}] #{pr.number} {pr.title}{labels}")

            issues = [
                (issue, state)
                for issue in data["issues"]
                if (state := issue.state_in_range(since, until)) is not None
            ]
            if issues:
                repo_lines.append("### Issues")
                for issue, state in issues:
                    labels = f" ({', '.join(issue.labels)})" if issue.labels else ""
                    repo_lines.append(
                        f"- [{state}] #{issue.number} {issue.title}{labels}"
                    )

            if repo_lines:
                lines.append(f"## {repo_name}")
                lines.extend(repo_lines)

        if len(lines) == 1:
            return "# GitHub アクティビティ\nアクティビティなし"

        return "\n".join(lines)
