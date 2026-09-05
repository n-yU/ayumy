"""JST timezone constant and the date window calculations built on it."""

from datetime import date, datetime, timedelta, timezone

from config import CONFIG

JST = timezone(timedelta(hours=9))


def date_to_range(target: date) -> tuple[datetime, datetime]:
    """Return the JST [00:00, next-day 00:00) window for the target date."""
    since = datetime(target.year, target.month, target.day, tzinfo=JST)
    until = since + timedelta(days=1)
    return since, until


def parse_target_dates(target_date: str) -> list[date]:
    """Parse a single `YYYY-MM-DD` or a `YYYY-MM-DD..YYYY-MM-DD` range into an inclusive date list.

    The range is capped by `CONFIG.pipeline.max_range_days` to bound activity fetch volume.

    Raises:
        ValueError: If the start date is after the end date, or the range exceeds the configured cap.
    """
    if ".." in target_date:
        start_str, end_str = target_date.split("..", 1)
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
        if start > end:
            raise ValueError(f"Start date {start} is after end date {end}")
        if (end - start).days >= CONFIG.pipeline.max_range_days:
            raise ValueError(
                f"Date range exceeds {CONFIG.pipeline.max_range_days} days: {start}..{end}"
            )
        dates = []
        current = start
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
        return dates
    return [date.fromisoformat(target_date)]


def get_target_date_range(
    source: str | None = None,
    target_date: str | None = None,
) -> tuple[datetime, datetime]:
    """Return the (since, until) window for activity fetching.

    `target_date` takes precedence and uses the first date's JST [00:00, next-day 00:00) window.
    Otherwise `source="manual"` returns today 00:00 ~ now, and any other value returns the prior day's JST window.
    """
    if target_date:
        first = target_date.split("..")[0]
        return date_to_range(date.fromisoformat(first))

    now_jst = datetime.now(JST)
    today_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    if source == "manual":
        return today_jst, now_jst

    yesterday_jst = today_jst - timedelta(days=1)
    return yesterday_jst, today_jst
