"""Tests for SummaryClient pure logic."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from config import CONFIG
from report import JST, SummaryUsage
from report.notice import Notice
from report.summarizer import (
    _SYSTEM_PROMPT,
    _VALUE_REPR_LIMIT,
    TOOL_NAME,
    SummaryClient,
    ValidationResult,
)
from report.tags import ALLOWED_TAG_NAMES, TAG_DEFINITIONS


def _make_client() -> SummaryClient:
    # Bypass Anthropic SDK init
    client = SummaryClient.__new__(SummaryClient)
    client._notice = Notice()
    return client


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

    def _set_response(self, blocks, input_tokens=100, output_tokens=50):
        message = MagicMock(content=blocks)
        message.usage.input_tokens = input_tokens
        message.usage.output_tokens = output_tokens
        self.client.client.messages.create.return_value = message

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

        result, _ = self.client.generate_summary(self.target, "gh", "sess")

        assert result == report

    def test_returns_usage_from_response(self):
        tool_use = MagicMock(type="tool_use", input={"repositories": []})
        tool_use.name = TOOL_NAME
        self._set_response([tool_use], input_tokens=10_000, output_tokens=2_000)

        _, usage = self.client.generate_summary(self.target, "gh", "sess")

        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        expected_spend = (
            10_000 * rates["input_usd_per_1m_tokens"]
            + 2_000 * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000
        assert isinstance(usage, SummaryUsage)
        assert usage.input_tokens == 10_000
        assert usage.output_tokens == 2_000
        assert usage.spend_usd == pytest.approx(expected_spend)

    def test_raises_when_no_tool_use_block(self):
        text_block = MagicMock(type="text")
        self._set_response([text_block])

        with pytest.raises(ValueError, match=TOOL_NAME):
            self.client.generate_summary(self.target, "gh", "sess")

    def _set_tool_use_input(self, payload):
        tool_use = MagicMock(type="tool_use", input=payload)
        tool_use.name = TOOL_NAME
        self._set_response([tool_use])

    def test_raises_when_repositories_field_missing(self):
        self._set_tool_use_input({})

        with pytest.raises(ValueError, match="repositories") as exc:
            self.client.generate_summary(self.target, "gh", "sess")
        assert "shape invalid" in str(exc.value)

    def test_raises_when_repositories_not_list(self):
        self._set_tool_use_input({"repositories": "oops"})

        with pytest.raises(ValueError, match="path=repositories") as exc:
            self.client.generate_summary(self.target, "gh", "sess")
        assert "got=str" in str(exc.value)

    def test_raises_when_repository_item_not_dict(self):
        self._set_tool_use_input({"repositories": ["not a dict"]})

        with pytest.raises(ValueError, match="path=repositories/0") as exc:
            self.client.generate_summary(self.target, "gh", "sess")
        assert "got=str" in str(exc.value)

    def test_raises_when_repository_field_type_mismatch(self):
        self._set_tool_use_input(
            {
                "repositories": [
                    {"name": 123, "summary": ["s1", "s2"], "tags": ["CI/CD"]}
                ]
            }
        )

        with pytest.raises(ValueError, match="path=repositories/0/name") as exc:
            self.client.generate_summary(self.target, "gh", "sess")
        assert "got=int" in str(exc.value)

    def test_truncates_long_instance_value_in_error_message(self):
        long_value = "x" * (_VALUE_REPR_LIMIT * 2)
        self._set_tool_use_input({"repositories": long_value})

        with pytest.raises(ValueError) as exc:
            self.client.generate_summary(self.target, "gh", "sess")

        summary_line = str(exc.value).split("\n", 1)[0]
        assert summary_line.endswith("...")
        value_portion = summary_line.split("value=", 1)[1]
        assert len(value_portion) <= _VALUE_REPR_LIMIT + len("...")


class TestValidationResult:
    def test_bool_empty(self):
        assert not ValidationResult()

    def test_bool_with_invalid_tags(self):
        result = ValidationResult()
        result.invalid_tags = {"repo": ["bad"]}
        assert result
