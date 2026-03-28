"""Tests for SummaryClient pure logic."""

from datetime import datetime

from report import JST
from report.summarizer import SummaryClient, ValidationResult


class TestBuildSystemPrompt:
    def test_includes_tags_and_statuses(self):
        result = SummaryClient._build_system_prompt(
            ["CI/CD", "Testing"], ["Active", "Idle"],
        )
        assert "- CI/CD" in result
        assert "- Testing" in result
        assert "- Active" in result
        assert "- Idle" in result


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
                "status": "Active",
            }],
        }
        result = SummaryClient.validate_report(report, ["CI/CD"], ["Active"])
        assert not result
        assert report["repositories"][0]["tags"] == ["CI/CD"]
        assert report["repositories"][0]["status"] == "Active"

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
                "status": "Active",
            }],
        }
        result = SummaryClient.validate_report(report, ["CI/CD"], ["Active"])
        assert result
        assert result.invalid_tags == {"repo": ["InvalidTag"]}
        assert report["repositories"][0]["tags"] == ["CI/CD"]

    def test_invalid_status_cleared(self):
        report = {
            "summary": "summary",
            "repositories": [{
                "name": "repo",
                "summary": "",
                "achievements": [],
                "ongoing": [],
                "claude_code": "",
                "tags": [],
                "status": "BadStatus",
            }],
        }
        result = SummaryClient.validate_report(report, [], ["Active"])
        assert result
        assert result.invalid_statuses == {"repo": "BadStatus"}
        assert report["repositories"][0]["status"] == ""

    def test_empty_status_treated_as_invalid(self):
        report = {
            "summary": "summary",
            "repositories": [{
                "name": "repo",
                "summary": "",
                "achievements": [],
                "ongoing": [],
                "claude_code": "",
                "tags": [],
                "status": "",
            }],
        }
        result = SummaryClient.validate_report(report, [], ["Active"])
        assert result
        assert result.invalid_statuses == {"repo": ""}


class TestValidationResult:
    def test_bool_empty(self):
        assert not ValidationResult()

    def test_bool_with_invalid_tags(self):
        r = ValidationResult()
        r.invalid_tags = {"repo": ["bad"]}
        assert r
