"""Run-scoped notice (warning) collection for Slack thread posting."""

import logging
from dataclasses import dataclass, field
from enum import StrEnum

_DEFAULT_LOGGER = logging.getLogger(__name__)


class NoticeSource(StrEnum):
    SESSION = "session"
    GITHUB = "github"
    NOTION = "notion"
    SUMMARY = "summary"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class NoticeEntry:
    source: NoticeSource
    title: str
    details: dict[str, str] = field(default_factory=dict)


class Notice:
    """Run-scoped notice collection; explicit `add()` keeps third-party logs out of the Slack thread."""

    def __init__(self) -> None:
        self._entries: list[NoticeEntry] = []

    def add(
        self,
        source: NoticeSource,
        title: str,
        *,
        logger: logging.Logger | None = None,
        exc_info: bool = False,
        **details: str,
    ) -> None:
        """Forwards to `logger.warning` so CloudWatch retains the message; pass `logger=` for the caller's namespace."""
        self._entries.append(
            NoticeEntry(source=source, title=title, details=dict(details))
        )
        target = logger or _DEFAULT_LOGGER
        if details:
            detail_str = ", ".join(f"{k}={v}" for k, v in details.items())
            target.warning("%s (%s)", title, detail_str, exc_info=exc_info)
        else:
            target.warning(title, exc_info=exc_info)

    def entries(self) -> list[NoticeEntry]:
        return list(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
