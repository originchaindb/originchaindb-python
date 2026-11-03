"""Encode user-supplied names as one URL path segment."""
from urllib.parse import quote

from .errors import OCValidationError


def segment(value: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise OCValidationError("Invalid path name")
    return quote(value, safe="")
