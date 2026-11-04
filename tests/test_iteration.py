import asyncio
import json

import httpx
import pytest

from originchain import AsyncClient, BatchWriteError, Client


@pytest.mark.asyncio
async def test_async_source_is_lazy_snapshotted_and_closed_on_early_stop():
    requests, consumed, closed = [], [], []

    async def source():
        row = {}
        try:
            for key in "abcde":
                consumed.append(key)
                row["id"] = key
                yield row
        finally:
            closed.append(True)

    def send(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"inserted": len(requests[-1])})

    async with AsyncClient(url="https://example.invalid", token="test", transport=httpx.MockTransport(send)) as db:
        stream = db.rows.write_batches("app.items", source(), batch_size=2)
        assert consumed == []
        result = await anext(stream)
        assert (result.offset, result.count, result.inserted) == (0, 2, 2)
        await stream.aclose()
    assert consumed == ["a", "b"] and closed == [True]
    assert requests == [[{"id": "a"}, {"id": "b"}]]


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_input_closes_source_without_sending():
    entered, closed = asyncio.Event(), []

    async def source():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield {"id": "never"}
        finally:
            closed.append(True)

    def unexpected(request):
        pytest.fail("canceled input reached transport")

    async with AsyncClient(url="https://example.invalid", token="test", transport=httpx.MockTransport(unexpected)) as db:
        stream = db.rows.write_batches("app.items", source())
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed == [True]


@pytest.mark.asyncio
async def test_async_cancel_during_write_is_not_replayed_or_relabelled():
    entered, calls = asyncio.Event(), []

    async def send(request):
        calls.append(request)
        entered.set()
        await asyncio.Event().wait()

    async with AsyncClient(url="https://example.invalid", token="test", transport=httpx.MockTransport(send)) as db:
        stream = db.rows.write_batches("app.items", [{"id": "a"}], batch_size=1)
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1


def test_source_failure_preserves_acknowledged_offset_and_no_buffered_write():
    calls, closed = [], []

    def source():
        try:
            yield {"id": "a"}
            yield {"id": "b"}
            yield {"id": "c"}
            raise ValueError("producer failure")
        finally:
            closed.append(True)

    def send(request):
        calls.append(request)
        return httpx.Response(200, json={"inserted": 2})

    with Client(url="https://example.invalid", token="test", transport=httpx.MockTransport(send)) as db:
        stream = db.rows.write_batches("app.items", source(), batch_size=2)
        assert next(stream).inserted == 2
        with pytest.raises(BatchWriteError) as failure:
            next(stream)
    error = failure.value
    assert (error.offset, error.count, error.outcome) == (2, 1, "not_sent")
    assert isinstance(error.__cause__, ValueError) and len(calls) == 1 and closed == [True]


@pytest.mark.asyncio
async def test_async_pages_keep_continuation_private_and_compare_utf8():
    requests = []
    # U+10000 sorts after U+E000 in UTF-8, but before it in UTF-16.
    keys = ["\ue000", "\U00010000"]

    def send(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"kind": "select", "rows": [{"id": k} for k in keys] if len(requests) == 1 else []})

    async with AsyncClient(url="https://example.invalid", token="test", transport=httpx.MockTransport(send)) as db:
        stream = db.rows.pages("app.items", "id", page_size=2)
        page = await anext(stream)
        page.rows[-1]["id"] = "tampered"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert requests[1]["params"] == [keys[-1]]
