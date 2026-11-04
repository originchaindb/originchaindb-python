"""Bounded batch writes and keyset reads. No prefetch or automatic replay."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterable, AsyncIterator, Callable, Iterable, Iterator, Mapping

from .errors import OCProtocolError


@dataclass(frozen=True)
class BatchResult:
    """One acknowledged batch. Offset and count identify its input range."""

    offset: int
    count: int
    inserted: int


class BatchWriteError(Exception):
    """Failed input range; outcome is 'not_sent' or 'unknown'. Never auto-replay."""

    def __init__(self, offset: int, count: int, outcome: str) -> None:
        self.offset, self.count, self.outcome = offset, count, outcome
        super().__init__(f"batch at input offset {offset} failed ({outcome})")


@dataclass(frozen=True)
class RowPage:
    """One page and its continuation value; None means no more pages."""

    rows: list[dict[str, Any]]
    next_cursor: str | None


def _limits(batch_size: int, max_bytes: int) -> None:
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be an integer from 1 to 10000")
    if type(max_bytes) is not int or not 2 <= max_bytes <= 8 * 1024 * 1024:
        raise ValueError("max_bytes must be an integer from 2 to 8388608")


def _encode(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    if not isinstance(row, Mapping):
        raise TypeError("each row must be an object")
    text = json.dumps(dict(row), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return json.loads(text), len(text.encode("utf-8"))


def _ack(value: Any, offset: int, count: int) -> BatchResult:
    inserted = value.get("inserted") if isinstance(value, dict) else None
    if type(inserted) is not int or not 0 <= inserted <= count:
        raise OCProtocolError("batch response must contain a valid inserted count")
    return BatchResult(offset, count, inserted)


def write_batches(
    send: Callable[[list[dict[str, Any]]], Any], rows: Iterable[Mapping[str, Any]], *,
    batch_size: int, max_bytes: int, cancelled: Callable[[], bool] | None,
) -> Iterator[BatchResult]:
    _limits(batch_size, max_bytes)
    source = iter(rows)
    batch: list[dict[str, Any]] = []
    size, offset = 2, 0

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError("batch iteration canceled")

    def flush() -> BatchResult:
        check_cancelled()
        try:
            return _ack(send(batch), offset, len(batch))
        except Exception as error:
            raise BatchWriteError(offset, len(batch), "unknown") from error

    try:
        while True:
            check_cancelled()
            try:
                item = next(source)
            except StopIteration:
                break
            except Exception as error:
                raise BatchWriteError(offset, len(batch), "not_sent") from error
            try:
                row, length = _encode(item)
                if length + 2 > max_bytes:
                    raise ValueError("row exceeds max_bytes")
            except Exception as error:
                raise BatchWriteError(offset, len(batch) + 1, "not_sent") from error
            if batch and size + 1 + length > max_bytes:
                result = flush()
                offset += len(batch)
                batch, size = [], 2
                yield result
            size += length + bool(batch)
            batch.append(row)
            if len(batch) == batch_size:
                result = flush()
                offset += len(batch)
                batch, size = [], 2
                yield result
        if batch:
            yield flush()
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()



async def write_batches_async(
    send: Callable[[list[dict[str, Any]]], Any],
    rows: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]], *,
    batch_size: int, max_bytes: int, cancelled: Callable[[], bool] | None,
) -> AsyncGenerator[BatchResult, None]:
    _limits(batch_size, max_bytes)
    source = rows.__aiter__() if isinstance(rows, AsyncIterable) else iter(rows)
    batch: list[dict[str, Any]] = []
    size, offset = 2, 0

    async def check_cancelled() -> None:
        await asyncio.sleep(0)
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError("batch iteration canceled")

    async def flush() -> BatchResult:
        await check_cancelled()
        try:
            return _ack(await send(batch), offset, len(batch))
        except Exception as error:
            raise BatchWriteError(offset, len(batch), "unknown") from error

    try:
        while True:
            await check_cancelled()
            try:
                item = await source.__anext__() if isinstance(source, AsyncIterator) else next(source)
            except (StopIteration, StopAsyncIteration):
                break
            except Exception as error:
                raise BatchWriteError(offset, len(batch), "not_sent") from error
            try:
                row, length = _encode(item)
                if length + 2 > max_bytes:
                    raise ValueError("row exceeds max_bytes")
            except Exception as error:
                raise BatchWriteError(offset, len(batch) + 1, "not_sent") from error
            if batch and size + 1 + length > max_bytes:
                result = await flush()
                offset += len(batch)
                batch, size = [], 2
                yield result
            size += length + bool(batch)
            batch.append(row)
            if len(batch) == batch_size:
                result = await flush()
                offset += len(batch)
                batch, size = [], 2
                yield result
        if batch:
            yield await flush()
    finally:
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()
        else:
            close = getattr(source, "close", None)
            if close is not None:
                close()



def page_query(schema: str, key: str, page_size: int, after: str | None) -> tuple[str, list[Any]]:
    parts = schema.split(".")
    if not 1 <= len(parts) <= 2 or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in [*parts, key]):
        raise ValueError("use a simple schema/table name and unique string-key column")
    _limits(page_size, 2)
    if after is not None and not isinstance(after, str):
        raise TypeError("after must be a string or None")
    table = ".".join('"' + p + '"' for p in parts)
    column = '"' + key + '"'
    where = f" WHERE {column} > $1" if after is not None else ""
    return f"SELECT * FROM {table}{where} ORDER BY {column} ASC LIMIT {page_size}", ([] if after is None else [after])


def decode_page(rows: list[dict[str, Any]], key: str, page_size: int, after: str | None) -> RowPage:
    if len(rows) > page_size:
        raise OCProtocolError("server exceeded page_size")
    previous = after.encode("utf-8") if after is not None else None
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str):
            raise OCProtocolError("pagination requires a non-null string key")
        encoded = value.encode("utf-8")
        if previous is not None and encoded <= previous:
            raise OCProtocolError("pagination key did not advance uniquely")
        previous = encoded
    return RowPage(rows, rows[-1][key] if len(rows) == page_size else None)
