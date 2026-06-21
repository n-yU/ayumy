"""Notion API client for writing daily report pages."""

import logging
from datetime import datetime

from notion_client import Client

from .. import (
    JST,
    ReportSummary,
    RepoSummary,
    SessionActivity,
    get_version,
)
from ..domain import CommitInfo, PullInfo
from ..github import GitHubActivity, RepoActivity
from .blocks import bulleted_link, bulleted_text, heading_2

logger = logging.getLogger(__name__)


def _is_in_range(iso_timestamp: str | None, since: datetime, until: datetime) -> bool:
    if not iso_timestamp:
        return False
    dt = datetime.fromisoformat(iso_timestamp)
    return since <= dt < until


class NotionClient:
    """Client for writing daily report pages to a Notion database."""

    def __init__(self, token: str, database_id: str) -> None:
        self.client = Client(auth=token)
        self.database_id = database_id
        self._data_source_id: str | None = None

    @property
    def data_source_id(self) -> str:
        """Return the cached data source ID.

        Raises:
            RuntimeError: If `init_data_source()` has not been called yet.
        """
        if self._data_source_id is None:
            raise RuntimeError(
                "data_source_id is not initialized; call init_data_source() first"
            )
        return self._data_source_id

    def init_data_source(self) -> None:
        """Resolves and caches the first data source ID; required before query/page-creation calls."""
        db = self.client.databases.retrieve(database_id=self.database_id)
        self._data_source_id = db["data_sources"][0]["id"]

    def _build_properties(
        self,
        target_date: datetime,
        repo_summary: RepoSummary,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
    ) -> dict:
        """Property payload per 'Spec: Database Properties'."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        title_str = f"{target_date.astimezone(JST).strftime('%y-%m-%d')}: {repo_summary['name']}"

        return {
            "Name": {"title": [{"type": "text", "text": {"content": title_str}}]},
            "Date": {"date": {"start": date_str}},
            "Repository": {"select": {"name": repo_summary["name"]}},
            "Tags": {"multi_select": [{"name": tag} for tag in repo_summary["tags"]]},
            "Commits": {"number": commits},
            "Merged": {"number": prs_merged},
            "Closed": {"number": issues_closed},
            "Sessions": {"number": claude_sessions},
            "Version": {
                "rich_text": [{"type": "text", "text": {"content": get_version()}}]
            },
        }

    def _build_status_sections(
        self,
        repo_name: str,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Per 'Spec: Page Body': Done = merged/closed PRs + closed issues; Todo = in-range open issues created in window; In Progress = remaining opens. Empty sections are omitted."""
        done: list[tuple[str, str, str]] = []
        in_progress: list[tuple[str, str, str]] = []
        todo: list[tuple[str, str, str]] = []

        for pr in repo_activity["pulls"]:
            label = pr.label(repo_name)
            if pr.state == "merged" or pr.state == "closed":
                done.append((label, pr.url, pr.done_prefix()))
            else:
                in_progress.append((label, pr.url, ""))

        for issue in repo_activity["issues"]:
            label = issue.label(repo_name)
            if issue.state == "closed":
                done.append((label, issue.url, issue.done_prefix()))
            elif _is_in_range(issue.created_at, since, until):
                todo.append((label, issue.url, ""))
            else:
                in_progress.append((label, issue.url, ""))

        blocks: list[dict] = []
        for heading, items in (
            ("Done", done),
            ("In Progress", in_progress),
            ("Todo", todo),
        ):
            if not items:
                continue
            blocks.append(heading_2(heading))
            for label, url, prefix in items:
                blocks.append(bulleted_link(label, url, prefix))

        return blocks

    def _build_timeline_section(
        self,
        repo_name: str,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Per 'Spec: Page Body': non-merge PR-linked commits nest under their PR via `children`; merge commits, direct commits, issue lines, and unmerged-closed PR lines sit at top level. PR header sorts before its merge commit at the same ts (secondary_priority=0)."""
        pulls = repo_activity["pulls"]
        issues = repo_activity["issues"]

        merge_sha_to_pr: dict[str, PullInfo] = {
            pr.merge_commit_sha: pr for pr in pulls if pr.merge_commit_sha
        }
        pr_by_number: dict[int, PullInfo] = {pr.number: pr for pr in pulls}
        pr_nested_commits: dict[int, list[CommitInfo]] = {n: [] for n in pr_by_number}

        # secondary_priority is 0 for PR headers so they sort before adjacent merge commits at the same ts
        entries: list[tuple[datetime, int, dict]] = []

        for c in repo_activity["commits"]:
            if not c.is_in_range(since, until):
                continue
            ts = datetime.fromisoformat(c.date)
            if c.sha in merge_sha_to_pr:
                entries.append((ts, 1, bulleted_link(c.label(), c.url, prefix="🔸 ")))
                continue
            # Pick smallest PR number for deterministic nesting independent of pull_numbers order
            attached_prs = [n for n in c.pull_numbers if n in pr_by_number]
            attached_pr = min(attached_prs) if attached_prs else None
            if attached_pr is not None:
                pr_nested_commits[attached_pr].append(c)
            else:
                entries.append((ts, 1, bulleted_link(c.label(), c.url, prefix="🔸 ")))

        for pr_number, pr in pr_by_number.items():
            nested = sorted(
                pr_nested_commits[pr_number],
                key=lambda c: datetime.fromisoformat(c.date),
            )
            candidates: list[datetime] = []
            if _is_in_range(pr.created_at, since, until):
                candidates.append(datetime.fromisoformat(pr.created_at))
            if nested:
                candidates.append(datetime.fromisoformat(nested[0].date))
            if _is_in_range(pr.merged_at, since, until):
                candidates.append(datetime.fromisoformat(pr.merged_at))
            if (
                pr.state == "closed"
                and not pr.merged_at
                and _is_in_range(pr.closed_at, since, until)
            ):
                candidates.append(datetime.fromisoformat(pr.closed_at))
            if not candidates:
                continue
            children = [bulleted_link(c.label(), c.url, prefix="🔸 ") for c in nested]
            entries.append(
                (
                    min(candidates),
                    0,
                    bulleted_link(
                        pr.label(repo_name),
                        pr.url,
                        prefix="🔀 ",
                        children=children or None,
                    ),
                )
            )
            # Unmerged-closed PR also gets a top-level close line
            if pr.state == "closed" and _is_in_range(pr.closed_at, since, until):
                entries.append(
                    (
                        datetime.fromisoformat(pr.closed_at),
                        1,
                        bulleted_link(
                            pr.label(repo_name),
                            pr.url,
                            prefix="⚠️ close: ",
                        ),
                    )
                )

        for issue in issues:
            label = issue.label(repo_name)
            if _is_in_range(issue.created_at, since, until):
                entries.append(
                    (
                        datetime.fromisoformat(issue.created_at),
                        1,
                        bulleted_link(label, issue.url, prefix="🟢 open: "),
                    )
                )
            if _is_in_range(issue.closed_at, since, until):
                entries.append(
                    (
                        datetime.fromisoformat(issue.closed_at),
                        1,
                        bulleted_link(
                            label,
                            issue.url,
                            prefix=issue.timeline_close_prefix(),
                        ),
                    )
                )

        if not entries:
            return []

        entries.sort(key=lambda e: (e[0], e[1]))

        return [heading_2("Timeline"), *(e[2] for e in entries)]

    def _build_children(
        self,
        repo_summary: RepoSummary,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        children: list[dict] = []

        children.append(heading_2("Summary"))
        for item in repo_summary["summary"]:
            children.append(bulleted_text(item))

        repo_name = repo_summary["name"]
        children.extend(
            self._build_status_sections(repo_name, repo_activity, since, until)
        )
        children.extend(
            self._build_timeline_section(repo_name, repo_activity, since, until)
        )

        return children

    def create_page(
        self,
        target_date: datetime,
        repo_summary: RepoSummary,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
    ) -> str:
        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=self._build_properties(
                target_date,
                repo_summary,
                commits,
                prs_merged,
                issues_closed,
                claude_sessions,
            ),
            children=self._build_children(repo_summary, repo_activity, since, until),
        )

        return page["url"]

    def _archive_existing_pages(self, target_date: datetime) -> int:
        """Ensures re-runs stay idempotent."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        # No pagination: daily page count won't exceed Notion's default page size (100)
        results = self.client.data_sources.query(
            data_source_id=self.data_source_id,
            filter={"property": "Date", "date": {"equals": date_str}},
        )

        count = 0
        for page in results["results"]:
            self.client.pages.update(page_id=page["id"], archived=True)
            count += 1

        return count

    def create_report_pages(
        self,
        target_date: datetime,
        since: datetime,
        until: datetime,
        report: ReportSummary,
        activity: GitHubActivity,
        session_activity: SessionActivity,
    ) -> list[tuple[str, str]]:
        """Archives same-date pages first to ensure re-runs stay idempotent."""
        archived = self._archive_existing_pages(target_date)
        if archived:
            date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
            logger.info("Archived %d existing page(s) for %s", archived, date_str)

        pages: list[tuple[str, str]] = []

        for repo_summary in report["repositories"]:
            repo_name = repo_summary["name"]

            if repo_name not in activity:
                logger.warning("Skipping unknown repo: %s", repo_name)
                continue

            repo_activity = activity.repos()[repo_name]

            commits = len(repo_activity["commits"])
            prs_merged = sum(1 for pr in repo_activity["pulls"] if pr.state == "merged")
            issues_closed = sum(
                1 for issue in repo_activity["issues"] if issue.state == "closed"
            )
            claude_sessions = len(session_activity.get(repo_name, []))

            url = self.create_page(
                target_date,
                repo_summary,
                repo_activity,
                since,
                until,
                commits,
                prs_merged,
                issues_closed,
                claude_sessions,
            )
            pages.append((repo_name, url))

        return pages
