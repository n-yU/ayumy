"""Tests for report.shared.inline notation parsing."""

import pytest

from report.shared import inline

from .._builders import OWNER, REPO

OTHER_REPO = "other-repo"


def _parse(text):
    return inline.parse_inline(text, OWNER, REPO)


def _url(repo, number):
    return f"https://github.com/{OWNER}/{repo}/issues/{number}"


class TestIssueUrl:
    def test_builds_issues_path_for_any_number(self):
        assert inline.issue_url(OWNER, REPO, "42") == _url(REPO, "42")


class TestParseInline:
    def test_returns_single_plain_segment_without_markup(self):
        assert _parse("プレーンな要約") == [inline.Segment("プレーンな要約")]

    def test_returns_nothing_for_empty_text(self):
        assert _parse("") == []

    def test_marks_backticked_span_as_code(self):
        assert _parse("`test_parser.py` を追加") == [
            inline.Segment("test_parser.py", code=True),
            inline.Segment(" を追加"),
        ]

    def test_marks_double_asterisk_span_as_bold(self):
        assert _parse("**重要** な変更") == [
            inline.Segment("重要", bold=True),
            inline.Segment(" な変更"),
        ]

    def test_links_bare_number_to_page_repository(self):
        assert _parse("実装・マージ #155") == [
            inline.Segment("実装・マージ "),
            inline.Segment("#155", url=_url(REPO, "155")),
        ]

    def test_links_qualified_number_to_named_repository(self):
        assert _parse(f"{OTHER_REPO}#12 を参照") == [
            inline.Segment(f"{OTHER_REPO}#12", url=_url(OTHER_REPO, "12")),
            inline.Segment(" を参照"),
        ]

    def test_leaves_markup_inside_code_span_unparsed(self):
        assert _parse("`**#12**`") == [inline.Segment("**#12**", code=True)]

    def test_leaves_backtick_inside_bold_span_literal(self):
        assert _parse("**`a`**") == [inline.Segment("`a`", bold=True)]

    def test_splits_multiple_markups_in_one_line(self):
        assert _parse("`a.py` を **修正** し #7 を close") == [
            inline.Segment("a.py", code=True),
            inline.Segment(" を "),
            inline.Segment("修正", bold=True),
            inline.Segment(" し "),
            inline.Segment("#7", url=_url(REPO, "7")),
            inline.Segment(" を close"),
        ]

    @pytest.mark.parametrize("text", ["色 #ff0000 を指定", "見出し # 1 を追加"])
    def test_leaves_non_reference_hash_as_plain_text(self, text):
        assert _parse(text) == [inline.Segment(text)]
