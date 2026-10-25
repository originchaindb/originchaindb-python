"""Conservative retry policy shared by synchronous and asynchronous clients."""

from __future__ import annotations

import math
import random
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

DEFAULT_MAX_RETRY_TIME = 30.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_HTTP_DATE = re.compile(
    r"(?:[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9:]{8} GMT|"
    r"[A-Za-z]+, [0-9]{2}-[A-Za-z]{3}-[0-9]{2} [0-9:]{8} GMT|"
    r"[A-Za-z]{3} [A-Za-z]{3} [ 0-9][0-9] [0-9:]{8} [0-9]{4})"
)


def validate_options(max_retries: int, max_retry_time: float) -> None:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 99:
        raise ValueError("max_retries must be an integer between 0 and 99")
    if isinstance(max_retry_time, bool) or not math.isfinite(max_retry_time) or max_retry_time <= 0:
        raise ValueError("max_retry_time must be finite and greater than zero")


def eligible(method: str, retry_safe: bool | None) -> bool:
    if retry_safe is not None and not isinstance(retry_safe, bool):
        raise ValueError("retry_safe must be a bool or None")
    return method.upper() in SAFE_METHODS if retry_safe is None else retry_safe


def backoff(attempt: int) -> float:
    return random.uniform(0.0, min(4.0, 0.25 * (2 ** min(attempt, 4))))


def retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse HTTP delay-seconds or an HTTP-date; preserve an explicit zero.

    Very large valid delays saturate above any practical retry budget, instead
    of overflowing and accidentally falling back to an immediate retry.
    """
    if value is None:
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        digits = value.lstrip("0") or "0"
        return 1e12 if len(digits) > 12 else float(digits)
    if not _HTTP_DATE.fullmatch(value):
        return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - (time.time() if now is None else now))
    except (ValueError, TypeError, OverflowError):
        return None
