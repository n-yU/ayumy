"""Tests for Notion client pure logic."""

from datetime import datetime
from unittest.mock import MagicMock

from report import JST
from report.notion import RICH_TEXT_LIMIT, NotionClient, _chunk_rich_text


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


class TestBuildProperties:
    def setup_method(self):
        self.client = NotionClient.__new__(NotionClient)
        self.client.client = MagicMock()
        self.client.database_id = "db-id"
        self.client._data_source_id = None

    def test_basic_properties(self):
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        repo_summary = {
            "name": "my-repo",
            "summary": "summary",
            "achievements": [],
            "ongoing": [],
            "claude_code": "",
            "tags": ["CI/CD", "Testing"],
            "status": "Active",
        }
        props = self.client._build_properties(target, repo_summary, 5, 2, 1, 3)

        assert props["Name"]["title"][0]["text"]["content"] == "26-03-28: my-repo"
        assert props["Date"]["date"]["start"] == "2026-03-28"
        assert props["Repository"]["select"]["name"] == "my-repo"
        assert len(props["Tags"]["multi_select"]) == 2
        assert {t["name"] for t in props["Tags"]["multi_select"]} == {"CI/CD", "Testing"}
        assert props["Commits"]["number"] == 5
        assert props["PRs Merged"]["number"] == 2
        assert props["Issues Closed"]["number"] == 1
        assert props["Claude Sessions"]["number"] == 3
        assert props["Status"]["select"]["name"] == "Active"
        assert props["Version"]["rich_text"][0]["text"]["content"] == "0.2.0"

    def test_empty_status_omitted(self):
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        repo_summary = {
            "name": "repo",
            "summary": "",
            "achievements": [],
            "ongoing": [],
            "claude_code": "",
            "tags": [],
            "status": "",
        }
        props = self.client._build_properties(target, repo_summary, 0, 0, 0, 0)
        assert "Status" not in props


class TestBuildChildren:
    def setup_method(self):
        self.client = NotionClient.__new__(NotionClient)
        self.client.client = MagicMock()
        self.client.database_id = "db-id"
        self.client._data_source_id = None

    def test_basic_structure(self):
        repo_summary = {
            "name": "repo",
            "summary": "repo summary",
            "achievements": ["merged PR"],
            "ongoing": ["open issue"],
            "claude_code": "session work",
            "tags": [],
            "status": "",
        }
        children = self.client._build_children("overall", repo_summary)

        types = [c["type"] for c in children]
        # paragraph (overall) + heading (概要) + paragraph (summary)
        # + heading (成果) + bullet + heading (継続中) + bullet
        # + heading (Claude Code) + paragraph
        assert types == [
            "paragraph", "heading_2", "paragraph",
            "heading_2", "bulleted_list_item",
            "heading_2", "bulleted_list_item",
            "heading_2", "paragraph",
        ]

        # Validate heading texts
        assert children[1]["heading_2"]["rich_text"][0]["text"]["content"] == "概要"
        assert children[3]["heading_2"]["rich_text"][0]["text"]["content"] == "成果"
        assert children[5]["heading_2"]["rich_text"][0]["text"]["content"] == "継続中の作業"
        assert children[7]["heading_2"]["rich_text"][0]["text"]["content"] == "Claude Code"

        # Validate representative paragraph and bullet contents
        assert children[2]["paragraph"]["rich_text"][0]["text"]["content"] == "repo summary"
        assert children[4]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "merged PR"

    def test_empty_sections_omitted(self):
        repo_summary = {
            "name": "repo",
            "summary": "summary",
            "achievements": [],
            "ongoing": [],
            "claude_code": "",
            "tags": [],
            "status": "",
        }
        children = self.client._build_children("overall", repo_summary)
        types = [c["type"] for c in children]
        # Only overall paragraph + 概要 heading + summary paragraph
        assert types == ["paragraph", "heading_2", "paragraph"]
