"""Tests for SummaryClient pure logic."""

from datetime import datetime

from report import JST
from report.summarizer import SummaryClient, ValidationResult


def _make_client(allowed_tags: list[str]) -> SummaryClient:
    """Build a SummaryClient without invoking Anthropic SDK init."""
    client = SummaryClient.__new__(SummaryClient)
    client.allowed_tags = allowed_tags
    return client


class TestBuildSystemPrompt:
    def test_includes_tags(self):
        client = _make_client(["CI/CD", "Testing"])
        result = client._build_system_prompt()
        assert "- CI/CD" in result
        assert "- Testing" in result


class TestBuildPrompt:
    def test_contains_date_and_sections(self):
        client = _make_client([])
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = client.build_prompt(target, "github data", "session data")

        assert "2026-03-28" in result
        assert "github data" in result
        assert "session data" in result


class TestValidateReport:
    def setup_method(self):
        self.client = _make_client(["CI/CD"])

    def test_valid_report_unchanged(self):
        report = {
            "repositories": [{
                "name": "repo",
                "summary": [],
                "tags": ["CI/CD"],
            }],
        }
        result = self.client.validate_report(report)
        assert not result
        assert report["repositories"][0]["tags"] == ["CI/CD"]

    def test_invalid_tags_removed(self):
        report = {
            "repositories": [{
                "name": "repo",
                "summary": [],
                "tags": ["CI/CD", "InvalidTag"],
            }],
        }
        result = self.client.validate_report(report)
        assert result
        assert result.invalid_tags == {"repo": ["InvalidTag"]}
        assert report["repositories"][0]["tags"] == ["CI/CD"]


class TestValidationResult:
    def test_bool_empty(self):
        assert not ValidationResult()

    def test_bool_with_invalid_tags(self):
        result = ValidationResult()
        result.invalid_tags = {"repo": ["bad"]}
        assert result
