"""Remaining execution time guard for Lambda runs."""

from collections.abc import Callable


class Approaching(Exception):
    """Raised when the remaining execution time drops below the configured margin."""


class Guard:
    """Checks the remaining execution time before steps that would not finish in time.

    Lambda kills the process on timeout without unwinding, so the queued Slack notifications never leave the process.
    Stopping while time remains keeps that delivery path intact.
    """

    def __init__(self, remaining_ms: Callable[[], int] | None, margin_sec: int) -> None:
        self._remaining_ms = remaining_ms
        self._margin_sec = margin_sec

    def check(self) -> None:
        """Raise when less than the margin remains, and do nothing outside Lambda.

        Raises:
            Approaching: If the remaining execution time is below the margin.
        """
        if self._remaining_ms is None:
            return
        remaining_sec = self._remaining_ms() / 1000
        if remaining_sec < self._margin_sec:
            raise Approaching(
                f"{remaining_sec:.1f}s left, below the {self._margin_sec}s margin"
            )
