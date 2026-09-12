"""Notion API client for writing daily report pages."""

import logging
from datetime import datetime

import notion_client

from config import CONFIG

from ..domain import activity, summary
from ..domain.session import SessionActivity
from ..shared import dates, env
from ..shared.notice import Notice, NoticeSource
from .blocks import bulleted_link, bulleted_text, heading_2

logger = logging.getLogger(__name__)


def _page_icon(repo_name: str) -> dict:
    """Build the native icon payload so the database listing distinguishes repositories at a glance."""
    icon = CONFIG.notion.icon_for(repo_name)
    return {"type": "icon", "icon": {"name": icon.name, "color": icon.color}}


def _queried_repository(page: dict) -> str | None:
    """Read the repository a queried page belongs to, or None when the property is unset."""
    select = page.get("properties", {}).get("Repository", {}).get("select")
    return select.get("name") if select else None


def _queried_regens(page: dict) -> int:
    """Read how many times a queried page had been rebuilt; pages predating the property count as never rebuilt."""
    value = page.get("properties", {}).get("Regens", {}).get("number")
    return int(value) if value is not None else 0


class Client:
    """Client for writing daily report pages to a Notion database."""

    def __init__(
        self, token: str, database_id: str, owner: str, notice: Notice | None = None
    ) -> None:
        self.client = notion_client.Client(auth=token)
        self.database_id = database_id
        self.owner = owner  # Resolves number references in summaries to GitHub URLs
        self._data_source_id: str | None = None
        self._notice = notice or Notice()

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
        repo_summary: summary.Repo,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
        regens: int,
    ) -> dict:
        """Property payload per 'Spec: Database Properties'."""
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        title_str = f"{target_date.astimezone(dates.JST).strftime('%y-%m-%d')}: {repo_summary['name']}"

        return {
            "Name": {"title": [{"type": "text", "text": {"content": title_str}}]},
            "Date": {"date": {"start": date_str}},
            "Repository": {"select": {"name": repo_summary["name"]}},
            "Tags": {"multi_select": [{"name": tag} for tag in repo_summary["tags"]]},
            "Commits": {"number": commits},
            "Merged": {"number": prs_merged},
            "Closed": {"number": issues_closed},
            "Sessions": {"number": claude_sessions},
            "Regens": {"number": regens},
            "Version": {
                "rich_text": [{"type": "text", "text": {"content": env.get_version()}}]
            },
        }

    def _build_status_sections(
        self,
        repo_activity: activity.Repo,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Per 'Spec: Page Body': Done = PRs / issues completed within the window; TODO = issues still open at `until` and created in window; In Progress = remaining opens. Items completed before the window and empty sections are omitted."""
        done: list[tuple[str, str, str]] = []
        in_progress: list[tuple[str, str, str]] = []
        todo: list[tuple[str, str, str]] = []

        for pr in repo_activity["pulls"]:
            label = pr.label()
            state = pr.state_in_range(since, until)
            if state is None:
                continue
            if state != "open":
                done.append((label, pr.url, pr.done_prefix()))
            else:
                in_progress.append((label, pr.url, pr.pending_prefix()))

        for issue in repo_activity["issues"]:
            label = issue.label()
            state = issue.state_in_range(since, until)
            if state is None:
                continue
            if state != "open":
                done.append((label, issue.url, issue.done_prefix()))
            elif activity.in_range(issue.created_at, since, until):
                todo.append((label, issue.url, issue.pending_prefix()))
            else:
                in_progress.append((label, issue.url, issue.pending_prefix()))

        blocks: list[dict] = []
        for heading, items in (
            ("Done", done),
            ("In Progress", in_progress),
            ("TODO", todo),
        ):
            if not items:
                continue
            blocks.append(heading_2(heading))
            for label, url, prefix in items:
                blocks.append(bulleted_link(label, url, prefix))

        return blocks

    def _build_timeline_section(
        self,
        repo_activity: activity.Repo,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        """Per 'Spec: Page Body': PR-linked commits nest under their PR via `children`, the merge commit last; direct commits and issue lines sit at top level. PR header sorts before other entries at the same ts (secondary_priority=0)."""
        pulls = repo_activity["pulls"]
        issues = repo_activity["issues"]

        merge_sha_to_pr: dict[str, activity.PullInfo] = {
            pr.merge_commit_sha: pr for pr in pulls if pr.merge_commit_sha
        }
        pr_by_number: dict[int, activity.PullInfo] = {pr.number: pr for pr in pulls}
        pr_nested_commits: dict[int, list[activity.CommitInfo]] = {
            n: [] for n in pr_by_number
        }
        pr_merge_commit: dict[int, activity.CommitInfo] = {}

        # secondary_priority is 0 for PR headers so they sort before other entries at the same ts
        entries: list[tuple[datetime, int, dict]] = []

        for c in repo_activity["commits"]:
            if not c.is_in_range(since, until):
                continue
            ts = datetime.fromisoformat(c.date)
            merged_pr = merge_sha_to_pr.get(c.sha)
            if merged_pr is not None:
                pr_merge_commit[merged_pr.number] = c
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
            merge_commit = pr_merge_commit.get(pr_number)
            candidates: list[datetime] = []
            if activity.in_range(pr.created_at, since, until):
                candidates.append(datetime.fromisoformat(pr.created_at))
            if nested:
                candidates.append(datetime.fromisoformat(nested[0].date))
            if activity.in_range(pr.merged_at, since, until):
                candidates.append(datetime.fromisoformat(pr.merged_at))
            # Keeps a merge commit whose author date lands in the window visible when the merge itself falls outside it
            if merge_commit is not None:
                candidates.append(datetime.fromisoformat(merge_commit.date))
            if (
                pr.state == "closed"
                and not pr.merged_at
                and activity.in_range(pr.closed_at, since, until)
            ):
                candidates.append(datetime.fromisoformat(pr.closed_at))
            if not candidates:
                continue
            children = [bulleted_link(c.label(), c.url, prefix="🔸 ") for c in nested]
            # Pinned last so the closing line of a PR reads the same regardless of merge style
            if merge_commit is not None:
                children.append(
                    bulleted_link(merge_commit.label(), merge_commit.url, prefix="🔻 ")
                )
            state = pr.state_in_range(since, until)
            entries.append(
                (
                    min(candidates),
                    0,
                    bulleted_link(
                        pr.label(),
                        pr.url,
                        prefix=pr.pending_prefix()
                        if state == "open"
                        else pr.done_prefix(),
                        children=children or None,
                    ),
                )
            )

        for issue in issues:
            label = issue.label()
            if activity.in_range(issue.created_at, since, until):
                entries.append(
                    (
                        datetime.fromisoformat(issue.created_at),
                        1,
                        bulleted_link(
                            label, issue.url, prefix=f"{issue.pending_prefix()}open: "
                        ),
                    )
                )
            if activity.in_range(issue.closed_at, since, until):
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
        repo_summary: summary.Repo,
        repo_activity: activity.Repo,
        since: datetime,
        until: datetime,
    ) -> list[dict]:
        children: list[dict] = []

        children.append(heading_2("Summary"))
        for item in repo_summary["summary"]:
            children.append(bulleted_text(item, self.owner, repo_summary["name"]))

        children.extend(self._build_status_sections(repo_activity, since, until))
        children.extend(self._build_timeline_section(repo_activity, since, until))

        return children

    def create_page(
        self,
        target_date: datetime,
        repo_summary: summary.Repo,
        repo_activity: activity.Repo,
        since: datetime,
        until: datetime,
        commits: int,
        prs_merged: int,
        issues_closed: int,
        claude_sessions: int,
        regens: int,
    ) -> str:
        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            icon=_page_icon(repo_summary["name"]),
            properties=self._build_properties(
                target_date,
                repo_summary,
                commits,
                prs_merged,
                issues_closed,
                claude_sessions,
                regens,
            ),
            children=self._build_children(repo_summary, repo_activity, since, until),
        )

        return page["url"]

    def _archive_existing_pages(self, target_date: datetime) -> dict[str, int]:
        """Archive every page already recorded for `target_date` so re-runs stay idempotent, returning the rebuild count to record per repository.

        Archiving moves pages to the trash, which queries can no longer reach, so the counts have to be carried over here rather than looked up at creation time.
        """
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        # No pagination: daily page count won't exceed Notion's default page size (100)
        results = self.client.data_sources.query(
            data_source_id=self.data_source_id,
            filter={"property": "Date", "date": {"equals": date_str}},
        )
        existing = results["results"]

        regens_by_repo: dict[str, int] = {}
        for page in existing:
            repo_name = _queried_repository(page)
            if repo_name is not None:
                # Duplicated rows would otherwise let the query order decide what gets carried
                regens_by_repo[repo_name] = max(
                    regens_by_repo.get(repo_name, 0), _queried_regens(page) + 1
                )
            self.client.pages.update(page_id=page["id"], archived=True)

        if existing:
            logger.info("Archived %d existing page(s) for %s", len(existing), date_str)

        return regens_by_repo

    def create_report_pages(
        self,
        target_date: datetime,
        since: datetime,
        until: datetime,
        report: summary.Report,
        github_activity: activity.GitHubActivity,
        session_activity: SessionActivity,
    ) -> list[tuple[str, str]]:
        """Create a report page for each summarized repository that has fetched activity, archiving same-date pages first to ensure re-runs stay idempotent."""
        regens_by_repo = self._archive_existing_pages(target_date)
        pages: list[tuple[str, str]] = []

        for repo_summary in report["repositories"]:
            repo_name = repo_summary["name"]

            if repo_name not in github_activity:
                self._notice.add(
                    NoticeSource.NOTION,
                    "Unknown repo skipped",
                    logger=logger,
                    repo=repo_name,
                )
                continue

            repo_activity = github_activity.repos()[repo_name]

            # Counted on the same window basis as the page body so the properties match what the page lists
            commits = sum(
                1 for c in repo_activity["commits"] if c.is_in_range(since, until)
            )
            prs_merged = sum(
                1
                for pr in repo_activity["pulls"]
                if pr.state_in_range(since, until) == "merged"
            )
            issues_closed = sum(
                1
                for issue in repo_activity["issues"]
                if issue.state_in_range(since, until) == "closed"
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
                regens_by_repo.get(repo_name, 0),
            )
            pages.append((repo_name, url))

        return pages
