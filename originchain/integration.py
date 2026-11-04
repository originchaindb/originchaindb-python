"""Optional local diagnostics and application row mapping. No telemetry exporter."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence, TypeVar

import httpx

from .errors import OCError

T = TypeVar("T")


class OCCredentialError(OCError):
    """The credential provider failed or returned an invalid token."""


class RowDecodeError(ValueError):
    """Conversion failed at a zero-based row. The cause is available explicitly."""

    def __init__(self, row: int) -> None:
        self.row = row
        super().__init__(f"cannot decode SQL row {row}")


def map_rows(rows: Sequence[Mapping[str, Any]], decode: Callable[[Mapping[str, Any]], T]) -> list[T]:
    """Apply an application decoder/validator to independent copies of rows.

    Python annotations alone do not validate values. Supply a validating model
    or function when runtime type checks are required. No partial list is returned.
    """
    result: list[T] = []
    for index, row in enumerate(rows):
        try:
            result.append(decode(deepcopy(dict(row))))
        except Exception as error:
            raise RowDecodeError(index) from error
    return result


@dataclass(frozen=True)
class RequestEvent:
    """Transport metadata only; duration is seconds. No URL, SQL, values or token.

    HTTP success can precede a result-decoding failure. Observers are local and
    should return quickly; exceptions from observers do not alter request results.
    """

    method: str
    operation: str
    duration: float
    status: int | None
    request_id: str | None
    error_kind: str | None


def observe(callback: Callable[[RequestEvent], None] | None, method: str, path: str,
            started: float, response: httpx.Response | None, error: BaseException | None) -> None:
    if callback is None:
        return
    operation = path.split("/", 1)[0]
    if operation not in {"sql", "rows", "schemas", "vector", "fts", "graph", "cypher", "usage", "query-metrics"}:
        operation = "other"
    status = response.status_code if response is not None else getattr(error, "status", None)
    request_id = response.headers.get("X-OC-Request-Id") if response is not None else getattr(error, "request_id", None)
    kind = None
    if error is not None:
        cause = error.__cause__ or error
        if isinstance(error, OCCredentialError):
            kind = "credential"
        elif isinstance(error, asyncio.CancelledError):
            kind = "canceled"
        elif isinstance(cause, (httpx.TimeoutException, TimeoutError)):
            kind = "timeout"
        elif isinstance(cause, httpx.RequestError):
            kind = "transport"
        elif status is not None:
            kind = {401: "authentication", 403: "permission", 429: "rate_limit"}.get(status, "http")
        else:
            kind = "client"
    try:
        callback(RequestEvent(method.upper(), operation, perf_counter() - started, status, request_id, kind))
    except Exception:
        pass
