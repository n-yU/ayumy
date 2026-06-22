"""Tests for Notion client pure logic."""

import re
from datetime import datetime
from unittest.mock import MagicMock

from report import JST, SessionActivity
from report.domain import CommitInfo, IssueInfo, PullInfo
from report.github import GitHubActivity
from report.notice import Notice
from report.notion import NotionClient

SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _commit(
    sha: str,
    message: str,
    hour: int = 10,
    minute: int = 0,
    pull_numbers: list[int] | None = None,
) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        message=message,
        author="user",
        date=datetime(2026, 3, 28, hour, minute, tzinfo=JST).isoformat(),
        url=f"https://github.com/n-yU/repo/commit/{sha}",
        pull_numbers=tuple(pull_numbers or ()),
    )


def _pr(
    number: int,
    title: str,
    state: str,
    *,
    draft: bool = False,
    created_at: str | None = None,
    merged_at: str | None = None,
    closed_at: str | None = None,
    merge_commit_sha: str | None = None,
) -> PullInfo:
    return PullInfo(
        number=number,
        title=title,
        state=state,
        author="user",
        labels=(),
        draft=draft,
        url=f"https://github.com/n-yU/repo/pull/{number}",
        created_at=created_at or "2026-03-27T09:00:00+09:00",
        merged_at=merged_at,
        closed_at=closed_at,
        merge_commit_sha=merge_commit_sha,
    )


def _issue(
    number: int,
    title: str,
    state: str,
    *,
    created_at: str | None = None,
    closed_at: str | None = None,
    state_reason: str | None = None,
) -> IssueInfo:
    return IssueInfo(
        number=number,
        title=title,
        state=state,
        author="user",
        labels=(),
        url=f"https://github.com/n-yU/repo/issues/{number}",
        created_at=created_at or "2026-03-27T09:00:00+09:00",
        closed_at=closed_at,
        state_reason=state_reason,
    )


def _make_client() -> NotionClient:
    client = NotionClient.__new__(NotionClient)
    client.client = MagicMock()
    client.database_id = "db-id"
    client._data_source_id = None
    client._notice = Notice()
    return client


class TestBuildProperties:
    def test_basic_properties(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        repo_summary = {
            "name": "my-repo",
            "summary": ["要点1", "要点2"],
            "tags": ["CI/CD", "Testing"],
        }
        props = client._build_properties(target, repo_summary, 5, 2, 1, 3)

        assert props["Name"]["title"][0]["text"]["content"] == "26-03-28: my-repo"
        assert props["Date"]["date"]["start"] == "2026-03-28"
        assert props["Repository"]["select"]["name"] == "my-repo"
        assert {t["name"] for t in props["Tags"]["multi_select"]} == {
            "CI/CD",
            "Testing",
        }
        assert props["Commits"]["number"] == 5
        assert props["Merged"]["number"] == 2
        assert props["Closed"]["number"] == 1
        assert props["Sessions"]["number"] == 3
        assert re.fullmatch(
            r"\d+\.\d+\.\d+", props["Version"]["rich_text"][0]["text"]["content"]
        )


class TestStatusSections:
    def test_done_collects_merged_and_closed(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(
                    1,
                    "Merged PR",
                    "merged",
                    merged_at="2026-03-28T10:00:00+09:00",
                    closed_at="2026-03-28T10:00:00+09:00",
                ),
                _pr(2, "Rejected PR", "closed", closed_at="2026-03-28T11:00:00+09:00"),
            ],
            "issues": [
                _issue(
                    10,
                    "Completed issue",
                    "closed",
                    closed_at="2026-03-28T12:00:00+09:00",
                    state_reason="completed",
                ),
                _issue(
                    11,
                    "Not planned",
                    "closed",
                    closed_at="2026-03-28T13:00:00+09:00",
                    state_reason="not_planned",
                ),
                _issue(
                    12,
                    "Duplicate",
                    "closed",
                    closed_at="2026-03-28T14:00:00+09:00",
                    state_reason="duplicate",
                ),
                _issue(
                    13, "Legacy closed", "closed", closed_at="2026-03-28T15:00:00+09:00"
                ),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)

        assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Done"

        # Each bullet has [prefix_text, linked_text]; verify prefix and label
        bullets = blocks[1:]
        prefixes = [
            b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in bullets
        ]
        labels = [
            b["bulleted_list_item"]["rich_text"][1]["text"]["content"] for b in bullets
        ]
        assert prefixes == [
            "✅ ",
            "⚠️ (closed) ",
            "✅ ",
            "⚠️ (not planned) ",
            "⚠️ (duplicate) ",
            "✅ ",
        ]
        assert labels == [
            "repo#1: Merged PR",
            "repo#2: Rejected PR",
            "repo#10: Completed issue",
            "repo#11: Not planned",
            "repo#12: Duplicate",
            "repo#13: Legacy closed",
        ]
        urls = [
            b["bulleted_list_item"]["rich_text"][1]["text"]["link"]["url"]
            for b in bullets
        ]
        assert urls == [
            "https://github.com/n-yU/repo/pull/1",
            "https://github.com/n-yU/repo/pull/2",
            "https://github.com/n-yU/repo/issues/10",
            "https://github.com/n-yU/repo/issues/11",
            "https://github.com/n-yU/repo/issues/12",
            "https://github.com/n-yU/repo/issues/13",
        ]

    def test_in_progress_includes_open_prs_and_existing_open_issues(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(3, "WIP", "open", draft=True),
                _pr(4, "Ready", "open", draft=False),
            ],
            "issues": [
                _issue(
                    20, "Old open issue", "open", created_at="2026-03-20T09:00:00+09:00"
                ),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        assert (
            blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "In Progress"
        )
        # No prefix on In Progress bullets
        bullets = blocks[1:]
        labels = [
            b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in bullets
        ]
        assert labels == ["repo#3: WIP", "repo#4: Ready", "repo#20: Old open issue"]
        urls = [
            b["bulleted_list_item"]["rich_text"][0]["text"]["link"]["url"]
            for b in bullets
        ]
        assert urls == [
            "https://github.com/n-yU/repo/pull/3",
            "https://github.com/n-yU/repo/pull/4",
            "https://github.com/n-yU/repo/issues/20",
        ]

    def test_todo_collects_newly_created_open_issues(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [],
            "issues": [
                _issue(30, "New issue", "open", created_at="2026-03-28T11:00:00+09:00"),
                _issue(
                    31, "Old open issue", "open", created_at="2026-03-20T09:00:00+09:00"
                ),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        # In Progress (old) heading + bullet, Todo heading + bullet
        headings = [
            b["heading_2"]["rich_text"][0]["text"]["content"]
            for b in blocks
            if b["type"] == "heading_2"
        ]
        assert headings == ["In Progress", "Todo"]
        # Verify each bullet links to its issue URL
        bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
        urls = [
            b["bulleted_list_item"]["rich_text"][0]["text"]["link"]["url"]
            for b in bullets
        ]
        assert urls == [
            "https://github.com/n-yU/repo/issues/31",  # In Progress (old)
            "https://github.com/n-yU/repo/issues/30",  # Todo (newly created)
        ]

    def test_empty_sections_omitted(self):
        client = _make_client()
        repo_activity = {"commits": [], "pulls": [], "issues": []}
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        assert blocks == []


def _bullet_text(block: dict) -> str:
    """Concatenate rich_text content of a bulleted_list_item block."""
    return "".join(
        rt["text"]["content"] for rt in block["bulleted_list_item"]["rich_text"]
    )


def _bullet_children(block: dict) -> list[dict]:
    return block["bulleted_list_item"].get("children", [])


class TestTimelineSection:
    def setup_method(self):
        self.client = _make_client()

    def test_pr_block_nests_non_merge_commits(self):
        repo_activity = {
            "commits": [
                _commit(
                    "aaa1111", "branch commit 1", hour=9, minute=30, pull_numbers=[1]
                ),
                _commit(
                    "bbb2222", "branch commit 2", hour=10, minute=0, pull_numbers=[1]
                ),
                _commit("ccc3333", "Squash merge", hour=11, minute=0, pull_numbers=[1]),
            ],
            "pulls": [
                _pr(
                    1,
                    "Add feature",
                    "merged",
                    created_at="2026-03-28T09:00:00+09:00",
                    merged_at="2026-03-28T11:00:00+09:00",
                    closed_at="2026-03-28T11:00:00+09:00",
                    merge_commit_sha="ccc3333",
                )
            ],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )

        # Heading + PR block + merge commit at top level
        assert [b["type"] for b in blocks] == [
            "heading_2",
            "bulleted_list_item",
            "bulleted_list_item",
        ]
        pr_block = blocks[1]
        assert _bullet_text(pr_block) == "🔀 repo#1: Add feature"
        children = _bullet_children(pr_block)
        assert [_bullet_text(c) for c in children] == [
            "🔸 aaa1111: branch commit 1",
            "🔸 bbb2222: branch commit 2",
        ]
        # Merge commit shows at top level
        merge_block = blocks[2]
        assert _bullet_text(merge_block) == "🔸 ccc3333: Squash merge"

    def test_pr_nested_commits_sort_by_datetime_across_timezones(self):
        # ISO string sort would put +00:00 before +09:00, but actual
        # chronological order is the opposite: 10:00+09:00 (= 01:00 UTC)
        # comes before 09:00+00:00 (= 18:00 JST)
        early = _commit(
            "aaa1111", "earlier in time", hour=10, minute=0, pull_numbers=[1]
        )
        late = CommitInfo(
            sha="bbb2222",
            message="later in time",
            author="user",
            date="2026-03-28T09:00:00+00:00",
            url="https://github.com/n-yU/repo/commit/bbb2222",
            pull_numbers=(1,),
        )
        repo_activity = {
            "commits": [late, early],
            "pulls": [_pr(1, "feat", "open", created_at="2026-03-28T09:00:00+09:00")],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        children = _bullet_children(blocks[1])
        assert [_bullet_text(c) for c in children] == [
            "🔸 aaa1111: earlier in time",
            "🔸 bbb2222: later in time",
        ]

    def test_commit_with_multiple_pulls_nests_under_smallest_pr(self):
        # Cherry-picked commit appears in two PRs; nesting must not
        # depend on pull_numbers input order
        repo_activity = {
            "commits": [
                _commit("aaa1111", "shared commit", hour=10, pull_numbers=[5, 2])
            ],
            "pulls": [
                _pr(2, "PR two", "open", created_at="2026-03-28T09:00:00+09:00"),
                _pr(5, "PR five", "open", created_at="2026-03-28T09:00:00+09:00"),
            ],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        pr_blocks = {_bullet_text(b): _bullet_children(b) for b in blocks[1:]}
        assert [_bullet_text(c) for c in pr_blocks["🔀 repo#2: PR two"]] == [
            "🔸 aaa1111: shared commit",
        ]
        assert pr_blocks["🔀 repo#5: PR five"] == []

    def test_pr_header_sorts_before_merge_commit_at_same_time(self):
        # PR opened before window; only the squash merge commit lands in range
        repo_activity = {
            "commits": [
                _commit("ccc3333", "Squash merge", hour=11, minute=0, pull_numbers=[2]),
            ],
            "pulls": [
                _pr(
                    2,
                    "Old PR finally merged",
                    "merged",
                    created_at="2026-03-20T09:00:00+09:00",
                    merged_at="2026-03-28T11:00:00+09:00",
                    closed_at="2026-03-28T11:00:00+09:00",
                    merge_commit_sha="ccc3333",
                )
            ],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        # PR header (no children) appears immediately before its merge commit
        assert _bullet_text(blocks[1]) == "🔀 repo#2: Old PR finally merged"
        assert _bullet_children(blocks[1]) == []
        assert _bullet_text(blocks[2]) == "🔸 ccc3333: Squash merge"

    def test_direct_commit_renders_at_top_level(self):
        repo_activity = {
            "commits": [_commit("ddd4444", "Direct commit", hour=10)],
            "pulls": [],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        assert _bullet_text(blocks[1]) == "🔸 ddd4444: Direct commit"

    def test_pr_with_no_in_range_activity_is_omitted(self):
        # Session-touched PR with no commits/state changes in range
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(8, "Touched", "open", created_at="2026-03-20T09:00:00+09:00")
            ],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        assert blocks == []

    def test_unmerged_pr_close_appears_at_top_level(self):
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(
                    7,
                    "Rejected",
                    "closed",
                    created_at="2026-03-27T09:00:00+09:00",
                    closed_at="2026-03-28T15:00:00+09:00",
                )
            ],
            "issues": [],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        # PR header (no commits) + top-level close line
        assert _bullet_text(blocks[1]) == "🔀 repo#7: Rejected"
        assert _bullet_text(blocks[2]) == "⚠️ close: repo#7: Rejected"

    def test_issue_open_and_close_emojis(self):
        repo_activity = {
            "commits": [],
            "pulls": [],
            "issues": [
                _issue(5, "New bug", "open", created_at="2026-03-28T08:00:00+09:00"),
                _issue(
                    6,
                    "Fixed",
                    "closed",
                    created_at="2026-03-20T09:00:00+09:00",
                    closed_at="2026-03-28T13:00:00+09:00",
                    state_reason="completed",
                ),
                _issue(
                    7,
                    "Won't fix",
                    "closed",
                    created_at="2026-03-20T09:00:00+09:00",
                    closed_at="2026-03-28T14:00:00+09:00",
                    state_reason="not_planned",
                ),
            ],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        texts = [_bullet_text(b) for b in blocks[1:]]
        assert texts == [
            "🟢 open: repo#5: New bug",
            "✅ close: repo#6: Fixed",
            "⚠️ close (not planned): repo#7: Won't fix",
        ]

    def test_chronological_order_across_types(self):
        repo_activity = {
            "commits": [
                _commit(
                    "aaa1111", "commit on PR#1", hour=10, minute=30, pull_numbers=[1]
                ),
                _commit("ddd4444", "Direct", hour=12),
            ],
            "pulls": [
                _pr(1, "Feature", "open", created_at="2026-03-28T09:00:00+09:00")
            ],
            "issues": [
                _issue(5, "Bug", "open", created_at="2026-03-28T08:00:00+09:00"),
            ],
        }
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        texts = [_bullet_text(b) for b in blocks[1:]]
        assert texts == [
            "🟢 open: repo#5: Bug",  # 08:00
            "🔀 repo#1: Feature",  # PR sort key 09:00 (open)
            "🔸 ddd4444: Direct",  # 12:00
        ]
        # PR block has its commit nested
        assert [_bullet_text(c) for c in _bullet_children(blocks[2])] == [
            "🔸 aaa1111: commit on PR#1",
        ]

    def test_omitted_when_no_entries(self):
        repo_activity = {"commits": [], "pulls": [], "issues": []}
        blocks = self.client._build_timeline_section(
            "repo", repo_activity, SINCE, UNTIL
        )
        assert blocks == []


class TestBuildChildren:
    def test_full_layout(self):
        client = _make_client()
        repo_summary = {
            "name": "repo",
            "summary": ["point one", "point two"],
            "tags": [],
        }
        repo_activity = {
            "commits": [_commit("deadbeef00", "Commit msg", hour=10, minute=0)],
            "pulls": [
                _pr(
                    1,
                    "Merged",
                    "merged",
                    merged_at="2026-03-28T11:00:00+09:00",
                    closed_at="2026-03-28T11:00:00+09:00",
                ),
            ],
            "issues": [
                _issue(5, "Open today", "open", created_at="2026-03-28T09:00:00+09:00"),
            ],
        }
        children = client._build_children(repo_summary, repo_activity, SINCE, UNTIL)
        types = [c["type"] for c in children]
        # Summary heading + 2 bullets,
        # Done heading + 1 bullet, Todo heading + 1 bullet,
        # Timeline heading + bullets (Issue open, PR header, direct commit)
        assert types == [
            "heading_2",
            "bulleted_list_item",
            "bulleted_list_item",
            "heading_2",
            "bulleted_list_item",
            "heading_2",
            "bulleted_list_item",
            "heading_2",
            "bulleted_list_item",
            "bulleted_list_item",
            "bulleted_list_item",
        ]

        headings = [
            c["heading_2"]["rich_text"][0]["text"]["content"]
            for c in children
            if c["type"] == "heading_2"
        ]
        assert headings == ["Summary", "Done", "Todo", "Timeline"]

    def test_only_summary_when_no_activity(self):
        client = _make_client()
        repo_summary = {"name": "repo", "summary": ["only point"], "tags": []}
        repo_activity = {"commits": [], "pulls": [], "issues": []}
        children = client._build_children(repo_summary, repo_activity, SINCE, UNTIL)
        types = [c["type"] for c in children]
        assert types == ["heading_2", "bulleted_list_item"]


class TestCreateReportPagesWiring:
    def test_passes_since_until_to_create_page(self):
        client = _make_client()
        client._archive_existing_pages = MagicMock(return_value=0)
        client.create_page = MagicMock(return_value="https://notion.so/page1")

        target_date = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": [{"name": "repo", "summary": [], "tags": []}]}
        activity = GitHubActivity({"repo": {"commits": [], "pulls": [], "issues": []}})
        sessions = SessionActivity({})

        client.create_report_pages(
            target_date, SINCE, UNTIL, report, activity, sessions
        )

        client.create_page.assert_called_once()
        args = client.create_page.call_args[0]
        # Signature: target_date, repo_summary, repo_activity, since, until, ...
        assert args[0] == target_date
        assert args[3] == SINCE
        assert args[4] == UNTIL
