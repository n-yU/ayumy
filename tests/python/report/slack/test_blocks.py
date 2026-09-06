"""Tests for slack.blocks primitives and mrkdwn conversion."""

from report.slack.blocks import (
    context_block,
    divider,
    escape_mrkdwn,
    header_block,
    section_block,
    to_mrkdwn,
)

from .._builders import OWNER


class TestEscapeMrkdwn:
    def test_escapes_ampersand_and_angle_brackets(self):
        assert escape_mrkdwn("a & b") == "a &amp; b"
        assert escape_mrkdwn("<!channel>") == "&lt;!channel&gt;"
        assert escape_mrkdwn("<@U123>") == "&lt;@U123&gt;"

    def test_passes_plain_text_through(self):
        assert escape_mrkdwn("plain text 日本語") == "plain text 日本語"


class TestToMrkdwn:
    def _convert(self, text):
        return to_mrkdwn(text, OWNER, "my-repo")

    def test_keeps_backticks_as_mrkdwn_inline_code(self):
        assert self._convert("`a.py` を追加") == "`a.py` を追加"

    def test_converts_double_asterisk_bold_to_single(self):
        assert self._convert("**重要** な変更") == "*重要* な変更"

    def test_links_number_reference(self):
        url = f"https://github.com/{OWNER}/my-repo/issues/155"
        assert self._convert("マージ #155") == f"マージ <{url}|#155>"

    def test_escapes_slack_specials_before_converting(self):
        assert self._convert("<!channel> **注意**") == "&lt;!channel&gt; *注意*"


class TestBlockPrimitives:
    def test_divider(self):
        assert divider() == {"type": "divider"}

    def test_header_block(self):
        assert header_block("📝 Daily Report (2026-03-28)") == {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📝 Daily Report (2026-03-28)",
            },
        }

    def test_section_block(self):
        assert section_block("hello") == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "hello"},
        }

    def test_context_block(self):
        assert context_block("ctx") == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "ctx"}],
        }
