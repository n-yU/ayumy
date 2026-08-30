"""Tests for report.inline notation parsing."""

import pytest

from report.inline import Segment, issue_url, parse_inline

from ._builders import OWNER, REPO

OTHER_REPO = "other-repo"


def _parse(text):
    return parse_inline(text, OWNER, REPO)


def _url(repo, number):
    return f"https://github.com/{OWNER}/{repo}/issues/{number}"


class TestIssueUrl:
    def test_builds_issues_path_for_any_number(self):
        assert issue_url(OWNER, REPO, "42") == _url(REPO, "42")


class TestParseInline:
    def test_returns_single_plain_segment_without_markup(self):
        assert _parse("プレーンな要約") == [Segment("プレーンな要約")]

    def test_returns_nothing_for_empty_text(self):
        assert _parse("") == []

    def test_marks_backticked_span_as_code(self):
        assert _parse("`test_parser.py` を追加") == [
            Segment("test_parser.py", code=True),
            Segment(" を追加"),
        ]

    def test_marks_double_asterisk_span_as_bold(self):
        assert _parse("**重要** な変更") == [
            Segment("重要", bold=True),
            Segment(" な変更"),
        ]

    def test_links_bare_number_to_page_repository(self):
        assert _parse("実装・マージ #155") == [
            Segment("実装・マージ "),
            Segment("#155", url=_url(REPO, "155")),
        ]

    def test_links_qualified_number_to_named_repository(self):
        assert _parse(f"{OTHER_REPO}#12 を参照") == [
            Segment(f"{OTHER_REPO}#12", url=_url(OTHER_REPO, "12")),
            Segment(" を参照"),
        ]

    def test_leaves_markup_inside_code_span_unparsed(self):
        assert _parse("`**#12**`") == [Segment("**#12**", code=True)]

    def test_leaves_backtick_inside_bold_span_literal(self):
        assert _parse("**`a`**") == [Segment("`a`", bold=True)]

    def test_splits_multiple_markups_in_one_line(self):
        assert _parse("`a.py` を **修正** し #7 を close") == [
            Segment("a.py", code=True),
            Segment(" を "),
            Segment("修正", bold=True),
            Segment(" し "),
            Segment("#7", url=_url(REPO, "7")),
            Segment(" を close"),
        ]

    @pytest.mark.parametrize("text", ["色 #ff0000 を指定", "見出し # 1 を追加"])
    def test_leaves_non_reference_hash_as_plain_text(self, text):
        assert _parse(text) == [Segment(text)]
