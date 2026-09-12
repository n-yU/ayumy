"""Tests for report.shared.timeout."""

import pytest

from report.shared import timeout


class TestGuard:
    def test_passes_when_margin_remains(self):
        guard = timeout.Guard(lambda: 90_000, 60)

        guard.check()

    def test_raises_when_below_margin(self):
        guard = timeout.Guard(lambda: 30_000, 60)

        with pytest.raises(timeout.Approaching):
            guard.check()

    def test_passes_at_exactly_margin(self):
        guard = timeout.Guard(lambda: 60_000, 60)

        guard.check()

    def test_stays_inert_without_a_clock(self):
        guard = timeout.Guard(None, 60)

        guard.check()

    def test_reports_remaining_and_margin(self):
        guard = timeout.Guard(lambda: 12_300, 60)

        with pytest.raises(timeout.Approaching, match=r"12\.3s left.*60s margin"):
            guard.check()

    def test_reads_the_clock_on_every_check(self):
        remaining = iter([90_000, 30_000])
        guard = timeout.Guard(lambda: next(remaining), 60)

        guard.check()

        with pytest.raises(timeout.Approaching):
            guard.check()
