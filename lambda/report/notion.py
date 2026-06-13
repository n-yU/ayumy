"""Notion API client for writing daily report pages."""

import logging
from datetime import datetime

from notion_client import Client

from . import (
    JST,
    GitHubActivity,
    RepoActivity,
    ReportSummary,
    RepoSummary,
    SessionActivity,
    get_version,
)
from .domain import CommitInfo, PullInfo

logger = logging.getLogger(__name__)


RICH_TEXT_LIMIT = 2000


def _chunk_rich_text(text: str) -> list[dict]:
    """Split `text` into rich_text objects each within Notion's per-item character limit."""
    return [
        {"type": "text", "text": {"content": text[i : i + RICH_TEXT_LIMIT]}}
        for i in range(0, len(text), RICH_TEXT_LIMIT)
    ]


def _linked_text(content: str, url: str) -> list[dict]:
    """Build hyperlinked rich_text objects, each within Notion's per-item character limit and sharing the same link."""
    return [
        {
            "type": "text",
            "text": {"content": content[i : i + RICH_TEXT_LIMIT], "link": {"url": url}},
        }
        for i in range(0, len(content), RICH_TEXT_LIMIT)
    ]


def _is_in_range(iso_timestamp: str | None, since: datetime, until: datetime) -> bool:
    """Check whether an ISO timestamp falls within [since, until)."""
    if not iso_timestamp:
        return False
    dt = datetime.fromisoformat(iso_timestamp)
    return since <= dt < until


def _bulleted_link(
    label: str,
    url: str,
    prefix: str = "",
    children: list[dict] | None = None,
) -> dict:
    """Build a `bulleted_list_item` block with an optional plain-text `prefix` rendered before the linked label and optional nested `children`."""
    rich_text: list[dict] = []
    if prefix:
        rich_text.append({"type": "text", "text": {"content": prefix}})
    rich_text.extend(_linked_text(label, url))
    body: dict = {"rich_text": rich_text}
    if children:
        body["children"] = children
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": body,
    }


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
        """Resolve and cache the database's first data source ID for later query and page-creation calls."""
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
        """Build the Notion page property payload (Spec.md §6.1) from report and per-repo activity counts."""
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
        """Build the Done / In Progress / Todo sections for one repository (Spec.md §6.2).

        Done collects merged or closed PRs and closed Issues.
        Todo collects Issues created within `[since, until)` that are still open.
        In Progress collects open PRs (including drafts) and any remaining open Issues.
        Sections with no entries are omitted from the output.
        """
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
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": heading}}],
                    },
                }
            )
            for label, url, prefix in items:
                blocks.append(_bulleted_link(label, url, prefix))

        return blocks

    def _build_timeline_section(
        self,
        repo_name: str,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Build the Timeline as a nested bullet list grouped by parent PR (Spec.md §6.2).

        Non-merge PR-linked commits nest under their PR block via `children`.
        Merge commits, direct commits, Issue open / close lines, and unmerged-closed PR lines sit at the top level.
        Entries are ordered by their earliest activity in `[since, until)`;
        when a PR header and its merge commit share a timestamp, the header is placed first.
        """
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
            if not _is_in_range(c.date, since, until):
                continue
            ts = datetime.fromisoformat(c.date)
            if c.sha in merge_sha_to_pr:
                entries.append((ts, 1, _bulleted_link(c.label(), c.url, prefix="🔸 ")))
                continue
            # Pick smallest PR number for deterministic nesting independent of pull_numbers order
            attached_prs = [n for n in c.pull_numbers if n in pr_by_number]
            attached_pr = min(attached_prs) if attached_prs else None
            if attached_pr is not None:
                pr_nested_commits[attached_pr].append(c)
            else:
                entries.append((ts, 1, _bulleted_link(c.label(), c.url, prefix="🔸 ")))

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
            children = [_bulleted_link(c.label(), c.url, prefix="🔸 ") for c in nested]
            entries.append(
                (
                    min(candidates),
                    0,
                    _bulleted_link(
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
                        _bulleted_link(
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
                        _bulleted_link(label, issue.url, prefix="🟢 open: "),
                    )
                )
            if _is_in_range(issue.closed_at, since, until):
                entries.append(
                    (
                        datetime.fromisoformat(issue.closed_at),
                        1,
                        _bulleted_link(
                            label,
                            issue.url,
                            prefix=issue.timeline_close_prefix(),
                        ),
                    )
                )

        if not entries:
            return []

        entries.sort(key=lambda e: (e[0], e[1]))

        return [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Timeline"}}],
                },
            },
            *(e[2] for e in entries),
        ]

    def _build_children(
        self,
        repo_summary: RepoSummary,
        repo_activity: RepoActivity,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Build the Notion page body blocks in Summary → status sections → Timeline order, omitting empty sections."""
        children: list[dict] = []

        children.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Summary"}}],
                },
            }
        )
        for item in repo_summary["summary"]:
            children.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": _chunk_rich_text(item),
                    },
                }
            )

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
        """Create a Notion page for one repository's daily report and return its URL."""
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
        """Archive existing pages for the target date so re-runs stay idempotent, returning the count archived."""
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
        """Create Notion pages for every repository in `report`, archiving any same-date pages first for idempotent re-runs."""
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

            commits = len(repo_activity.get("commits", []))
            prs_merged = sum(
                1 for pr in repo_activity.get("pulls", []) if pr.state == "merged"
            )
            issues_closed = sum(
                1
                for issue in repo_activity.get("issues", [])
                if issue.state == "closed"
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
