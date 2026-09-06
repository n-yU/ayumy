"""Tests for environment variable and VERSION file readers."""

import re

import pytest

from report.shared import env


class TestGetVersion:
    def test_returns_semver_string(self):
        version = env.get_version()
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)


class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert env.require_env("TEST_VAR") == "hello"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            env.require_env("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            env.require_env("TEST_VAR")
