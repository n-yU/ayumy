"""Tests for report package core utilities."""

import os

import pytest

from report import require_env


class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert require_env("TEST_VAR") == "hello"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")
