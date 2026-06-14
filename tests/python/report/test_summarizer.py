"""Tests for SummaryClient pure logic."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from report import JST
from report.summarizer import _SYSTEM_PROMPT, TOOL_NAME, SummaryClient, ValidationResult
from report.tags import ALLOWED_TAG_NAMES, TAG_DEFINITIONS


def _make_client() -> SummaryClient:
    # Bypass Anthropic SDK init
    return SummaryClient.__new__(SummaryClient)


class TestBuildToolSchema:
    def test_enforces_allowed_tags_via_enum(self):
        client = _make_client()
        schema = client._build_tool_schema()
        repo_props = schema["input_schema"]["properties"]["repositories"]["items"][
            "properties"
        ]
        assert repo_props["tags"]["items"]["enum"] == list(ALLOWED_TAG_NAMES)

    def test_tags_field_carries_description_per_tag(self):
        client = _make_client()
        schema = client._build_tool_schema()
        tags_field = schema["input_schema"]["properties"]["repositories"]["items"][
            "properties"
        ]["tags"]
        for tag in TAG_DEFINITIONS:
            assert tag.name in tags_field["description"]
            assert tag.description in tags_field["description"]


class TestSystemPrompt:
    def test_lists_each_tag_with_description(self):
        for tag in TAG_DEFINITIONS:
            assert tag.name in _SYSTEM_PROMPT
            assert tag.description in _SYSTEM_PROMPT


class TestBuildPrompt:
    def test_contains_date_and_sections(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = client.build_prompt(target, "github data", "session data")

        assert "2026-03-28" in result
        assert "github data" in result
        assert "session data" in result


class TestValidateReport:
    def setup_method(self):
        self.client = _make_client()
        self.valid_tag = ALLOWED_TAG_NAMES[0]

    def test_valid_report_unchanged(self):
        report = {
            "repositories": [
                {
                    "name": "repo",
                    "summary": [],
                    "tags": [self.valid_tag],
                }
            ],
        }
        result = self.client.validate_report(report)
        assert not result
        assert report["repositories"][0]["tags"] == [self.valid_tag]

    def test_invalid_tags_removed(self):
        report = {
            "repositories": [
                {
                    "name": "repo",
                    "summary": [],
                    "tags": [self.valid_tag, "InvalidTag"],
                }
            ],
        }
        result = self.client.validate_report(report)
        assert result
        assert result.invalid_tags == {"repo": ["InvalidTag"]}
        assert report["repositories"][0]["tags"] == [self.valid_tag]


class TestGenerateSummary:
    def setup_method(self):
        self.client = _make_client()
        self.client.client = MagicMock()
        self.target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

    def _set_response(self, blocks):
        self.client.client.messages.create.return_value = MagicMock(content=blocks)

    def test_returns_input_from_tool_use_block(self):
        report = {
            "repositories": [{"name": "r", "summary": ["s1", "s2"], "tags": ["CI/CD"]}]
        }
        tool_use = MagicMock(type="tool_use", input=report)
        tool_use.name = (
            TOOL_NAME  # `name` kwarg on MagicMock sets the mock label, not attr
        )
        text_block = MagicMock(type="text")
        self._set_response([text_block, tool_use])

        result = self.client.generate_summary(self.target, "gh", "sess")

        assert result == report

    def test_raises_when_no_tool_use_block(self):
        text_block = MagicMock(type="text")
        self._set_response([text_block])

        with pytest.raises(ValueError, match=TOOL_NAME):
            self.client.generate_summary(self.target, "gh", "sess")


class TestValidationResult:
    def test_bool_empty(self):
        assert not ValidationResult()

    def test_bool_with_invalid_tags(self):
        result = ValidationResult()
        result.invalid_tags = {"repo": ["bad"]}
        assert result
