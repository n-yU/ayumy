"""Tests for Notion client pure logic."""

import re
from datetime import datetime
from unittest.mock import MagicMock

from report import JST
from report.notion import RICH_TEXT_LIMIT, NotionClient, _chunk_rich_text, _linked_text


SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _commit(sha: str, message: str, hour: int = 10, minute: int = 0) -> dict:
    return {
        "sha": sha,
        "message": message,
        "author": "user",
        "date": datetime(2026, 3, 28, hour, minute, tzinfo=JST).isoformat(),
        "url": f"https://github.com/n-yU/repo/commit/{sha}",
    }


def _pr(
    number: int,
    title: str,
    state: str,
    *,
    draft: bool = False,
    created_at: str | None = None,
    merged_at: str | None = None,
    closed_at: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "author": "user",
        "labels": [],
        "draft": draft,
        "url": f"https://github.com/n-yU/repo/pull/{number}",
        "created_at": created_at or "2026-03-27T09:00:00+09:00",
        "merged_at": merged_at,
        "closed_at": closed_at,
    }


def _issue(
    number: int,
    title: str,
    state: str,
    *,
    created_at: str | None = None,
    closed_at: str | None = None,
    state_reason: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "author": "user",
        "labels": [],
        "url": f"https://github.com/n-yU/repo/issues/{number}",
        "created_at": created_at or "2026-03-27T09:00:00+09:00",
        "closed_at": closed_at,
        "state_reason": state_reason,
    }


def _make_client() -> NotionClient:
    client = NotionClient.__new__(NotionClient)
    client.client = MagicMock()
    client.database_id = "db-id"
    client._data_source_id = None
    return client


class TestChunkRichText:
    def test_short_text_single_chunk(self):
        result = _chunk_rich_text("hello")
        assert result == [{"type": "text", "text": {"content": "hello"}}]

    def test_text_exceeding_limit(self):
        overflow = RICH_TEXT_LIMIT // 4
        text = "a" * (RICH_TEXT_LIMIT * 2 + overflow)
        result = _chunk_rich_text(text)
        assert len(result) == 3
        assert len(result[0]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[1]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[2]["text"]["content"]) == overflow

    def test_empty_text(self):
        result = _chunk_rich_text("")
        assert result == []


class TestLinkedText:
    def test_short_content_single_chunk(self):
        result = _linked_text("repo#1: title", "https://example.com/1")
        assert result == [{
            "type": "text",
            "text": {"content": "repo#1: title", "link": {"url": "https://example.com/1"}},
        }]

    def test_long_content_split_with_shared_link(self):
        long_label = "x" * (RICH_TEXT_LIMIT * 2 + 100)
        result = _linked_text(long_label, "https://example.com/long")
        assert len(result) == 3
        assert all(item["text"]["link"]["url"] == "https://example.com/long" for item in result)
        assert len(result[0]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[1]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[2]["text"]["content"]) == 100


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
        assert {t["name"] for t in props["Tags"]["multi_select"]} == {"CI/CD", "Testing"}
        assert props["Commits"]["number"] == 5
        assert props["Merged"]["number"] == 2
        assert props["Closed"]["number"] == 1
        assert props["Sessions"]["number"] == 3
        assert re.fullmatch(r"\d+\.\d+\.\d+", props["Version"]["rich_text"][0]["text"]["content"])


class TestStatusSections:
    def test_done_collects_merged_and_closed(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(1, "Merged PR", "merged",
                    merged_at="2026-03-28T10:00:00+09:00",
                    closed_at="2026-03-28T10:00:00+09:00"),
                _pr(2, "Rejected PR", "closed",
                    closed_at="2026-03-28T11:00:00+09:00"),
            ],
            "issues": [
                _issue(10, "Completed issue", "closed",
                       closed_at="2026-03-28T12:00:00+09:00",
                       state_reason="completed"),
                _issue(11, "Not planned", "closed",
                       closed_at="2026-03-28T13:00:00+09:00",
                       state_reason="not_planned"),
                _issue(12, "Duplicate", "closed",
                       closed_at="2026-03-28T14:00:00+09:00",
                       state_reason="duplicate"),
                _issue(13, "Legacy closed", "closed",
                       closed_at="2026-03-28T15:00:00+09:00"),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)

        assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Done"

        # Each bullet has [prefix_text, linked_text]; verify prefix and label
        bullets = blocks[1:]
        prefixes = [b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in bullets]
        labels = [b["bulleted_list_item"]["rich_text"][1]["text"]["content"] for b in bullets]
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

    def test_in_progress_includes_open_prs_and_existing_open_issues(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [
                _pr(3, "WIP", "open", draft=True),
                _pr(4, "Ready", "open", draft=False),
            ],
            "issues": [
                _issue(20, "Old open issue", "open",
                       created_at="2026-03-20T09:00:00+09:00"),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "In Progress"
        # No prefix on In Progress bullets
        labels = [b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in blocks[1:]]
        assert labels == ["repo#3: WIP", "repo#4: Ready", "repo#20: Old open issue"]

    def test_todo_collects_newly_created_open_issues(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [],
            "issues": [
                _issue(30, "New issue", "open",
                       created_at="2026-03-28T11:00:00+09:00"),
                _issue(31, "Old open issue", "open",
                       created_at="2026-03-20T09:00:00+09:00"),
            ],
        }
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        # In Progress (old) heading + bullet, Todo heading + bullet
        headings = [
            b["heading_2"]["rich_text"][0]["text"]["content"]
            for b in blocks if b["type"] == "heading_2"
        ]
        assert headings == ["In Progress", "Todo"]

    def test_empty_sections_omitted(self):
        client = _make_client()
        repo_activity = {"commits": [], "pulls": [], "issues": []}
        blocks = client._build_status_sections("repo", repo_activity, SINCE, UNTIL)
        assert blocks == []


class TestTimelineSection:
    def test_table_with_chronological_events(self):
        client = _make_client()
        repo_activity = {
            "commits": [_commit("abc1234567", "Fix typo", hour=10, minute=30)],
            "pulls": [
                _pr(1, "Add feature", "merged",
                    created_at="2026-03-28T09:00:00+09:00",
                    merged_at="2026-03-28T11:00:00+09:00",
                    closed_at="2026-03-28T11:00:00+09:00"),
            ],
            "issues": [
                _issue(5, "Bug", "open",
                       created_at="2026-03-28T08:00:00+09:00"),
            ],
        }
        blocks = client._build_timeline_section("repo", repo_activity, SINCE, UNTIL)

        assert [b["type"] for b in blocks] == ["heading_2", "table"]
        assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Timeline"

        table = blocks[1]["table"]
        assert table["table_width"] == 3
        assert table["has_column_header"] is True

        rows = table["children"]
        # header + 4 events (issue opened, PR opened, commit, PR merged)
        assert len(rows) == 5
        header_cells = [c[0]["text"]["content"] for c in rows[0]["table_row"]["cells"]]
        assert header_cells == ["Time", "Type", "Detail"]

        # Verify chronological order: 08:00 issue, 09:00 PR opened, 10:30 commit, 11:00 PR merged
        times = [r["table_row"]["cells"][0][0]["text"]["content"] for r in rows[1:]]
        assert times == ["08:00", "09:00", "10:30", "11:00"]

        types = [r["table_row"]["cells"][1][0]["text"]["content"] for r in rows[1:]]
        assert types == ["Issue opened", "PR opened", "commit", "PR merged"]

        details = [r["table_row"]["cells"][2][0]["text"]["content"] for r in rows[1:]]
        assert details[0] == "repo#5: Bug"
        assert details[1] == "repo#1: Add feature"
        assert details[2] == "abc1234: Fix typo"
        assert details[3] == "repo#1: Add feature"

    def test_pr_closed_without_merge_emits_pr_closed(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [_pr(7, "Rejected", "closed",
                          created_at="2026-03-27T09:00:00+09:00",
                          closed_at="2026-03-28T15:00:00+09:00")],
            "issues": [],
        }
        blocks = client._build_timeline_section("repo", repo_activity, SINCE, UNTIL)
        rows = blocks[1]["table"]["children"]
        assert rows[1]["table_row"]["cells"][1][0]["text"]["content"] == "PR closed"

    def test_issue_closed_emits_issue_closed_row(self):
        client = _make_client()
        repo_activity = {
            "commits": [],
            "pulls": [],
            "issues": [_issue(9, "Old bug", "closed",
                              created_at="2026-03-20T09:00:00+09:00",
                              closed_at="2026-03-28T13:30:00+09:00")],
        }
        blocks = client._build_timeline_section("repo", repo_activity, SINCE, UNTIL)
        rows = blocks[1]["table"]["children"]
        # Header + 1 data row (Issue closed only; created_at is out of range)
        assert len(rows) == 2
        cells = rows[1]["table_row"]["cells"]
        assert cells[0][0]["text"]["content"] == "13:30"
        assert cells[1][0]["text"]["content"] == "Issue closed"
        assert cells[2][0]["text"]["content"] == "repo#9: Old bug"

    def test_omitted_when_no_events_in_range(self):
        client = _make_client()
        # Open PR with no transition timestamps in range
        repo_activity = {
            "commits": [],
            "pulls": [_pr(8, "Touched", "open",
                          created_at="2026-03-20T09:00:00+09:00")],
            "issues": [],
        }
        blocks = client._build_timeline_section("repo", repo_activity, SINCE, UNTIL)
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
                _pr(1, "Merged", "merged",
                    merged_at="2026-03-28T11:00:00+09:00",
                    closed_at="2026-03-28T11:00:00+09:00"),
            ],
            "issues": [
                _issue(5, "Open today", "open",
                       created_at="2026-03-28T09:00:00+09:00"),
            ],
        }
        children = client._build_children(repo_summary, repo_activity, SINCE, UNTIL)
        types = [c["type"] for c in children]
        # Summary heading + 2 bullets,
        # Done heading + 1 bullet, Todo heading + 1 bullet,
        # Timeline heading + table
        assert types == [
            "heading_2", "bulleted_list_item", "bulleted_list_item",
            "heading_2", "bulleted_list_item",
            "heading_2", "bulleted_list_item",
            "heading_2", "table",
        ]

        headings = [
            c["heading_2"]["rich_text"][0]["text"]["content"]
            for c in children if c["type"] == "heading_2"
        ]
        assert headings == ["Summary", "Done", "Todo", "Timeline"]

    def test_only_summary_when_no_activity(self):
        client = _make_client()
        repo_summary = {"name": "repo", "summary": ["only point"], "tags": []}
        repo_activity = {"commits": [], "pulls": [], "issues": []}
        children = client._build_children(repo_summary, repo_activity, SINCE, UNTIL)
        types = [c["type"] for c in children]
        assert types == ["heading_2", "bulleted_list_item"]
