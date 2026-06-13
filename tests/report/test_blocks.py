"""Tests for notion.blocks primitives."""

from report.notion.blocks import (
    RICH_TEXT_LIMIT,
    bulleted_link,
    chunk_rich_text,
    linked_text,
)


class TestChunkRichText:
    def test_short_text_single_chunk(self):
        result = chunk_rich_text("hello")
        assert result == [{"type": "text", "text": {"content": "hello"}}]

    def test_text_exceeding_limit(self):
        overflow = RICH_TEXT_LIMIT // 4
        text = "a" * (RICH_TEXT_LIMIT * 2 + overflow)
        result = chunk_rich_text(text)
        assert len(result) == 3
        assert len(result[0]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[1]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[2]["text"]["content"]) == overflow

    def test_empty_text(self):
        result = chunk_rich_text("")
        assert result == []


class TestLinkedText:
    def test_short_content_single_chunk(self):
        result = linked_text("repo#1: title", "https://example.com/1")
        assert result == [
            {
                "type": "text",
                "text": {
                    "content": "repo#1: title",
                    "link": {"url": "https://example.com/1"},
                },
            }
        ]

    def test_long_content_split_with_shared_link(self):
        long_label = "x" * (RICH_TEXT_LIMIT * 2 + 100)
        result = linked_text(long_label, "https://example.com/long")
        assert len(result) == 3
        assert all(
            item["text"]["link"]["url"] == "https://example.com/long" for item in result
        )
        assert len(result[0]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[1]["text"]["content"]) == RICH_TEXT_LIMIT
        assert len(result[2]["text"]["content"]) == 100


class TestBulletedLink:
    def test_no_prefix_no_children(self):
        result = bulleted_link("repo#1: title", "https://example.com/1")
        assert result == {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": "repo#1: title",
                            "link": {"url": "https://example.com/1"},
                        },
                    }
                ],
            },
        }

    def test_prefix_prepends_plain_text(self):
        result = bulleted_link("repo#1: title", "https://example.com/1", prefix="✅ ")
        rich_text = result["bulleted_list_item"]["rich_text"]
        assert rich_text[0] == {"type": "text", "text": {"content": "✅ "}}
        assert rich_text[1]["text"]["link"] == {"url": "https://example.com/1"}

    def test_children_attached_when_provided(self):
        child = bulleted_link("inner", "https://example.com/inner")
        result = bulleted_link("outer", "https://example.com/outer", children=[child])
        assert result["bulleted_list_item"]["children"] == [child]

    def test_children_omitted_when_falsy(self):
        result = bulleted_link("outer", "https://example.com/outer", children=[])
        assert "children" not in result["bulleted_list_item"]
