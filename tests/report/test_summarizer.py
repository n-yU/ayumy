"""Tests for SummaryClient pure logic."""

from datetime import datetime

from report import JST
from report.summarizer import SummaryClient, ValidationResult


class TestBuildSystemPrompt:
    def test_includes_tags(self):
        result = SummaryClient._build_system_prompt(["CI/CD", "Testing"])
        assert "- CI/CD" in result
        assert "- Testing" in result


class TestBuildPrompt:
    def test_contains_date_and_sections(self):
        client = SummaryClient.__new__(SummaryClient)
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = client.build_prompt(target, "github data", "session data")

        assert "2026-03-28" in result
        assert "github data" in result
        assert "session data" in result


class TestValidateReport:
    def test_valid_report_unchanged(self):
        report = {
            "summary": "summary",
            "repositories": [{
                "name": "repo",
                "summary": "",
                "achievements": [],
                "ongoing": [],
                "claude_code": "",
                "tags": ["CI/CD"],
            }],
        }
        result = SummaryClient.validate_report(report, ["CI/CD"])
        assert not result
        assert report["repositories"][0]["tags"] == ["CI/CD"]

    def test_invalid_tags_removed(self):
        report = {
            "summary": "summary",
            "repositories": [{
                "name": "repo",
                "summary": "",
                "achievements": [],
                "ongoing": [],
                "claude_code": "",
                "tags": ["CI/CD", "InvalidTag"],
            }],
        }
        result = SummaryClient.validate_report(report, ["CI/CD"])
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
