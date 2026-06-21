"""Warning aggregator for run-scoped warning collection."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WarningEntry:
    module: str
    title: str
    details: dict[str, str] = field(default_factory=dict)


class WarningAggregator:
    """Collects warnings emitted during a single pipeline run for later thread posting.

    Explicit `add()` calls are used instead of a logging.Handler to avoid sweeping third-party warnings into the Slack thread.
    """

    def __init__(self) -> None:
        self._entries: list[WarningEntry] = []

    def add(self, module: str, title: str, **details: str) -> None:
        """`details` should be short identifiers (commit SHA, PR number, S3 key, etc.) — not full traceback or message bodies."""
        self._entries.append(WarningEntry(module=module, title=title, details=details))

    def entries(self) -> list[WarningEntry]:
        return list(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
