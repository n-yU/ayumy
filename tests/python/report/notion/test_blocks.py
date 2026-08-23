"""Tests for notion.blocks primitives."""

import pytest

from report.notion.blocks import (
    RICH_TEXT_LIMIT,
    bulleted_link,
    bulleted_text,
    chunk_rich_text,
    heading_2,
    linked_text,
)

URL = "https://example.com/1"


def _linked(text: str) -> list[dict]:
    return linked_text(text, URL)


@pytest.mark.parametrize(
    "build", [chunk_rich_text, _linked], ids=["chunk_rich_text", "linked_text"]
)
@pytest.mark.parametrize(
    "length,expected_lengths",
    [
        pytest.param(0, [], id="empty"),
        pytest.param(5, [5], id="within_limit"),
        pytest.param(
            RICH_TEXT_LIMIT * 2 + 100,
            [RICH_TEXT_LIMIT, RICH_TEXT_LIMIT, 100],
            id="over_limit",
        ),
    ],
)
def test_splits_content_at_rich_text_limit(build, length, expected_lengths):
    items = build("a" * length)
    assert [len(item["text"]["content"]) for item in items] == expected_lengths


def test_chunk_rich_text_builds_plain_items():
    assert chunk_rich_text("hello") == [{"type": "text", "text": {"content": "hello"}}]


def test_linked_text_repeats_link_on_every_chunk():
    items = _linked("x" * (RICH_TEXT_LIMIT + 1))
    assert [item["text"]["link"] for item in items] == [{"url": URL}] * 2


def test_linked_text_builds_linked_items():
    assert _linked("repo#1: title") == [
        {
            "type": "text",
            "text": {"content": "repo#1: title", "link": {"url": URL}},
        }
    ]


class TestBulletedLink:
    def test_builds_block_without_prefix_or_children(self):
        assert bulleted_link("repo#1: title", URL) == {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "repo#1: title", "link": {"url": URL}},
                    }
                ],
            },
        }

    def test_prefix_prepends_plain_text(self):
        rich_text = bulleted_link("repo#1: title", URL, prefix="✅ ")[
            "bulleted_list_item"
        ]["rich_text"]
        assert rich_text[0] == {"type": "text", "text": {"content": "✅ "}}
        assert rich_text[1]["text"]["link"] == {"url": URL}

    def test_children_attached_when_provided(self):
        child = bulleted_link("inner", "https://example.com/inner")
        result = bulleted_link("outer", URL, children=[child])
        assert result["bulleted_list_item"]["children"] == [child]

    @pytest.mark.parametrize("children", [None, []], ids=["none", "empty"])
    def test_children_omitted_when_absent(self, children):
        result = bulleted_link("outer", URL, children=children)
        assert "children" not in result["bulleted_list_item"]


class TestBulletedText:
    def test_builds_block_from_plain_text(self):
        assert bulleted_text("hello") == {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": "hello"}}],
            },
        }

    def test_chunks_text_over_limit(self):
        rich_text = bulleted_text("a" * (RICH_TEXT_LIMIT + 50))["bulleted_list_item"][
            "rich_text"
        ]
        assert [len(rt["text"]["content"]) for rt in rich_text] == [RICH_TEXT_LIMIT, 50]


def test_heading_2_builds_block_from_plain_text():
    assert heading_2("Summary") == {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "Summary"}}],
        },
    }
