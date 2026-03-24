"""Notion API client for writing daily report pages."""

import sys
from datetime import datetime

from notion_client import Client

from . import JST, GitHubActivity, RepoSummary, ReportSummary, SessionActivity


RICH_TEXT_LIMIT = 2000


def _chunk_rich_text(text: str) -> list[dict]:
    """Split text into rich_text objects respecting Notion's per-item limit.

    Args:
        text: Text content to split

    Returns:
        A list of rich_text objects, each within RICH_TEXT_LIMIT chars
    """
    return [
        {"type": "text", "text": {"content": text[i:i + RICH_TEXT_LIMIT]}}
        for i in range(0, len(text), RICH_TEXT_LIMIT)
    ]


class NotionClient:
    """Client for writing daily report pages to a Notion database."""

    def __init__(self, token: str, database_id: str) -> None:
        """Initialize the client with Notion credentials.

        Args:
            token: Notion Internal Integration token
            database_id: Target Notion database ID
        """
        self.client = Client(auth=token)
        self.database_id = database_id
        self._data_source_id: str | None = None

    def fetch_allowlists(self) -> tuple[list[str], list[str]]:
        """Fetch allowed tags and statuses from the database schema.

        Reads the Tags (multi-select) and Status (select) property
        options defined in the Notion database via the data sources API.

        Returns:
            A tuple of (allowed_tags, allowed_statuses) as string lists
        """
        db = self.client.databases.retrieve(database_id=self.database_id)
        self._data_source_id = db["data_sources"][0]["id"]
        ds = self.client.data_sources.retrieve(data_source_id=self._data_source_id)
        properties = ds["properties"]

        tags = [
            opt["name"]
            for opt in properties["Tags"]["multi_select"]["options"]
        ]
        statuses = [
            opt["name"]
            for opt in properties["Status"]["select"]["options"]
        ]
        return tags, statuses

    def _build_properties(
        self,
        target_date: datetime,
        repo_summary: RepoSummary,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
    ) -> dict:
        """Build Notion page properties from report data.

        Args:
            target_date: The target date for the report
            repo_summary: Per-repository summary from Claude API
            commits: Number of commits in this repo
            prs_merged: Number of merged PRs in this repo
            issues_closed: Number of closed issues in this repo
            claude_sessions: Number of Claude Code sessions for this repo

        Returns:
            A dict of Notion page properties
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        properties: dict = {
            "Name": {"title": [{"type": "text", "text": {"content": repo_summary["name"]}}]},
            "Date": {"date": {"start": date_str}},
            "Repository": {"select": {"name": repo_summary["name"]}},
            "Tags": {"multi_select": [{"name": tag} for tag in repo_summary["tags"]]},
            "Commits": {"number": commits},
            "PRs Merged": {"number": prs_merged},
            "Issues Closed": {"number": issues_closed},
            "Claude Sessions": {"number": claude_sessions},
        }

        if repo_summary["status"]:
            properties["Status"] = {"select": {"name": repo_summary["status"]}}

        return properties

    def _build_children(
        self,
        overall_summary: str,
        repo_summary: RepoSummary,
    ) -> list[dict]:
        """Build Notion page body blocks from report data.

        Args:
            overall_summary: Overall daily summary text
            repo_summary: Per-repository summary from Claude API

        Returns:
            A list of Notion block objects (heading_2, paragraph,
            bulleted_list_item)
        """
        children: list[dict] = []

        # Overall summary
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _chunk_rich_text(overall_summary)},
        })

        # Repository summary
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "概要"}}],
            },
        })
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _chunk_rich_text(repo_summary["summary"])},
        })

        # Achievements
        if repo_summary["achievements"]:
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "成果"}}],
                },
            })
            for item in repo_summary["achievements"]:
                children.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": _chunk_rich_text(item),
                    },
                })

        # Ongoing work
        if repo_summary["ongoing"]:
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "継続中の作業"}}],
                },
            })
            for item in repo_summary["ongoing"]:
                children.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": _chunk_rich_text(item),
                    },
                })

        # Claude Code
        if repo_summary["claude_code"]:
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Claude Code"}}],
                },
            })
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _chunk_rich_text(repo_summary["claude_code"])},
            })

        return children

    def create_page(
        self,
        target_date: datetime,
        repo_summary: RepoSummary,
        overall_summary: str,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
    ) -> str:
        """Create a Notion page for a single repository's daily report.

        Args:
            target_date: The target date for the report
            repo_summary: Per-repository summary from Claude API
            overall_summary: Overall daily summary text
            commits: Number of commits in this repo
            prs_merged: Number of merged PRs in this repo
            issues_closed: Number of closed issues in this repo
            claude_sessions: Number of Claude Code sessions for this repo

        Returns:
            The URL of the created Notion page
        """
        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=self._build_properties(
                target_date, repo_summary, commits, prs_merged, issues_closed, claude_sessions,
            ),
            children=self._build_children(overall_summary, repo_summary),
        )

        return page["url"]

    def _archive_existing_pages(self, target_date: datetime) -> int:
        """Archive existing pages for the target date.

        Queries the database for pages matching the target date and
        archives them to prevent duplicates on re-runs.

        Args:
            target_date: The target date to match

        Returns:
            The number of pages archived
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        # No pagination: daily page count won't exceed Notion's default page size (100)
        results = self.client.data_sources.query(
            data_source_id=self._data_source_id,
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
        report: ReportSummary,
        activity: GitHubActivity,
        session_activity: SessionActivity,
    ) -> list[tuple[str, str]]:
        """Create Notion pages for all repositories in the report.

        Archives (soft-deletes) existing pages for the target date before
        creating new ones to ensure idempotent re-runs.

        Args:
            target_date: The target date for the report
            report: Full report summary from Claude API
            activity: GitHub activity data keyed by repo name
            session_activity: Claude Code session data keyed by repo name

        Returns:
            A list of (repo_name, page_url) tuples for created pages
        """
        archived = self._archive_existing_pages(target_date)
        if archived:
            date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
            print(f"Archived {archived} existing page(s) for {date_str}", file=sys.stderr)

        pages: list[tuple[str, str]] = []

        for repo_summary in report["repositories"]:
            repo_name = repo_summary["name"]

            # Skip repos not found in activity (no matching GitHub repo)
            if repo_name not in activity:
                print(f"Skipping unknown repo: {repo_name}", file=sys.stderr)
                continue

            repo_activity = activity.repos()[repo_name]

            commits = len(repo_activity.get("commits", []))
            prs_merged = sum(
                1 for pr in repo_activity.get("pulls", []) if pr["state"] == "merged"
            )
            issues_closed = sum(
                1 for issue in repo_activity.get("issues", []) if issue["state"] == "closed"
            )
            claude_sessions = len(session_activity.get(repo_name, []))

            url = self.create_page(
                target_date,
                repo_summary,
                report["summary"],
                commits,
                prs_merged,
                issues_closed,
                claude_sessions,
            )
            pages.append((repo_name, url))

        return pages
