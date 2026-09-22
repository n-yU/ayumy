"""Tests for report.summarizer.client logic."""

from datetime import datetime
from typing import cast
from unittest.mock import MagicMock

import anthropic
import pytest

from config import CONFIG
from report import summarizer
from report.domain import summary
from report.shared import dates
from report.shared.notice import Notice
from report.summarizer import client as summarizer_client
from report.summarizer import resources, tags


def _make_client() -> summarizer.Client:
    # Bypass Anthropic SDK init
    client = summarizer.Client.__new__(summarizer.Client)
    client._notice = Notice()
    return client


class TestBuildPrompt:
    def test_contains_date_and_sections(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)
        result = client.build_prompt(target, "github data", "session data")

        assert "2026-03-28" in result
        assert "<github_activity>\ngithub data\n</github_activity>" in result
        assert "<claude_code_sessions>\nsession data\n</claude_code_sessions>" in result


class TestValidateReport:
    def setup_method(self):
        self.client = _make_client()
        self.valid_tag = tags.ALLOWED_NAMES[0]

    def test_valid_report_unchanged(self):
        report: summary.Report = {
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
        report: summary.Report = {
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
        self.target = datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)

    def _set_response(self, blocks, input_tokens=100, output_tokens=50):
        message = MagicMock(content=blocks)
        message.usage.input_tokens = input_tokens
        message.usage.output_tokens = output_tokens
        create = cast(MagicMock, self.client.client.messages.create)
        create.return_value = message

    def test_returns_input_from_tool_use_block(self):
        report = {
            "repositories": [{"name": "r", "summary": ["s1", "s2"], "tags": ["CI/CD"]}]
        }
        tool_use = MagicMock(spec=anthropic.types.ToolUseBlock, input=report)
        tool_use.name = (
            resources.TOOL_NAME  # `name` kwarg on MagicMock sets the mock label, not attr
        )
        text_block = MagicMock(spec=anthropic.types.TextBlock)
        self._set_response([text_block, tool_use])

        result, _ = self.client.generate_summary(self.target, "gh", "sess")

        assert result == report

    def _call_kwargs(self) -> dict:
        tool_use = MagicMock(
            spec=anthropic.types.ToolUseBlock, input={"repositories": []}
        )
        tool_use.name = resources.TOOL_NAME
        self._set_response([tool_use])
        self.client.generate_summary(self.target, "gh", "sess")
        create = cast(MagicMock, self.client.client.messages.create)
        return dict(create.call_args.kwargs)

    def test_sends_prompt_and_tool_from_resources(self):
        kwargs = self._call_kwargs()

        assert kwargs["system"] == resources.SYSTEM_PROMPT
        assert kwargs["tools"] == [resources.TOOL_DEFINITION]
        assert kwargs["tool_choice"] == {"type": "tool", "name": resources.TOOL_NAME}
        assert kwargs["messages"] == [
            {
                "role": "user",
                "content": self.client.build_prompt(self.target, "gh", "sess"),
            }
        ]

    def test_sends_configured_model_and_token_cap(self):
        kwargs = self._call_kwargs()

        assert kwargs["model"] == CONFIG.claude.model
        assert kwargs["max_tokens"] == CONFIG.claude.max_tokens

    def test_returns_usage_from_response(self):
        tool_use = MagicMock(
            spec=anthropic.types.ToolUseBlock, input={"repositories": []}
        )
        tool_use.name = resources.TOOL_NAME
        self._set_response([tool_use], input_tokens=10_000, output_tokens=2_000)

        _, usage = self.client.generate_summary(self.target, "gh", "sess")

        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        expected_spend = (
            10_000 * rates["input_usd_per_1m_tokens"]
            + 2_000 * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000
        assert isinstance(usage, summary.Usage)
        assert usage.input_tokens == 10_000
        assert usage.output_tokens == 2_000
        assert usage.spend_usd == pytest.approx(expected_spend)

    def test_raises_when_no_tool_use_block(self):
        text_block = MagicMock(spec=anthropic.types.TextBlock)
        self._set_response([text_block])

        with pytest.raises(ValueError, match=resources.TOOL_NAME):
            self.client.generate_summary(self.target, "gh", "sess")

    def _set_tool_use_input(self, payload):
        tool_use = MagicMock(spec=anthropic.types.ToolUseBlock, input=payload)
        tool_use.name = resources.TOOL_NAME
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
        long_value = "x" * (summarizer_client._VALUE_REPR_LIMIT * 2)
        self._set_tool_use_input({"repositories": long_value})

        with pytest.raises(ValueError) as exc:
            self.client.generate_summary(self.target, "gh", "sess")

        summary_line = str(exc.value).split("\n", 1)[0]
        assert summary_line.endswith("...")
        value_portion = summary_line.split("value=", 1)[1]
        assert len(value_portion) <= summarizer_client._VALUE_REPR_LIMIT + len("...")


class TestValidationResult:
    def test_bool_empty(self):
        assert not summarizer.ValidationResult()

    def test_bool_with_invalid_tags(self):
        result = summarizer.ValidationResult()
        result.invalid_tags = {"repo": ["bad"]}
        assert result
