"""Utility helpers for the Jarvis AI Agent.

Shared utility functions used across multiple modules.

Time convention used throughout the project:
  * The database stores every datetime as *naive UTC* (SQLite has no time zones).
  * Anything the user types without an offset ("tomorrow 10:00") is *local time*.
  * API responses send datetimes with an explicit UTC offset so the browser
    can show them in the user's local time.
"""

import json
import re
import logging
from datetime import date, datetime, time, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON action object from an LLM response.

    Handles, in order:
      1. a ```json fenced block
      2. the whole reply being JSON
      3. JSON embedded in prose ("Sure! {"action": ...}")

    Returns the dict if it has an 'action' key, else None.
    """
    if not text:
        return None

    candidates = []
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "action" in data:
            return data
    return None


def utc_now() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def to_db_datetime(value: datetime) -> datetime:
    """Convert a datetime to naive UTC for storage.

    Naive input is treated as the user's local time.
    """
    if value.tzinfo is None:
        value = value.astimezone()  # attach the local time zone
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def from_db_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """Attach UTC to a naive datetime read from the database."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_user_datetime(value: Any) -> datetime:
    """Parse an ISO string (or datetime) from the user/LLM into naive UTC.

    Raises ValueError for unparseable input.
    """
    if isinstance(value, datetime):
        return to_db_datetime(value)
    if not isinstance(value, str):
        raise ValueError(f"Expected an ISO date-time string, got {value!r}")
    return to_db_datetime(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))


def local_today() -> date:
    """Today's date in the local time zone of the machine running Jarvis."""
    return datetime.now().astimezone().date()


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Start/end of a *local* calendar day, as naive UTC (for DB queries)."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day, time.max).astimezone()
    return to_db_datetime(start), to_db_datetime(end)


def local_now_description() -> str:
    """Human-readable local date/time with UTC offset, for the LLM prompt."""
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return f"{now.strftime('%A, %Y-%m-%d %H:%M')} (UTC{offset[:3]}:{offset[3:]})"


def format_duration(seconds: int) -> str:
    """Format seconds as '2h 15m', '45m' or '30s'."""
    if seconds < 60:
        return f"{seconds}s"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def truncate(text: str, max_length: int = 100) -> str:
    """Truncate text to max_length with an ellipsis."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def sanitize_input(text: str) -> str:
    """Strip surrounding whitespace and control characters (keeps \\n and \\t)."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return cleaned.strip()


def money(amount: float) -> str:
    """Format an amount with the configured currency symbol."""
    from app.config import get_settings
    return f"{get_settings().currency_symbol}{amount:,.2f}"
