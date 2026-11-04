import asyncio
from dataclasses import dataclass

import httpx
import pytest

from originchain import AsyncClient, Client, OCCredentialError, OCError, RowDecodeError
from originchain.models import SqlExecResult


def test_rotation_has_no_auth_replay_and_observation_is_redacted():
    calls, tokens, events = [], iter(["first-secret", "second-secret"]), []

    def request(req):
        calls.append(req.headers["Authorization"])
        return httpx.Response(401 if len(calls) == 1 else 200,
                              json={"code": "token_expired"} if len(calls) == 1 else {"kind": "select", "rows": [{"value": 7}]},
                              headers={"X-OC-Request-Id": "request-123"})

    with Client(url="https://example.invalid/private-db", token_provider=lambda: next(tokens),
                transport=httpx.MockTransport(request), on_request=events.append) as db:
        with pytest.raises(OCError) as failure:
            db.sql.run("private SQL")
        assert failure.value.status == 401
        assert calls == ["Bearer first-secret"]
        assert db.sql.run("private SQL").rows == [{"value": 7}]
    assert calls == ["Bearer first-secret", "Bearer second-secret"]
    assert [event.error_kind for event in events] == ["authentication", None]
    assert events[1].request_id == "request-123"
    assert events[1].operation == "sql" and events[1].duration > 0
    for secret in ("secret", "private-db", "private SQL", "example.invalid"):
        assert secret not in repr(events)


@pytest.mark.parametrize("value", ["", "bad\nheader", None])
def test_invalid_provider_value_never_reaches_transport(value):
    def unexpected(request):
        pytest.fail("invalid token reached the transport")
    with Client(url="https://example.invalid", token_provider=lambda: value,
                transport=httpx.MockTransport(unexpected)) as db:
        with pytest.raises(OCCredentialError):
            db.usage()


def test_provider_errors_are_redacted_and_observer_failures_ignored():
    events = []
    def provider():
        raise RuntimeError("secret detail")
    def observe(event):
        events.append(event)
        raise RuntimeError("observer")
    with Client(url="https://example.invalid", token_provider=provider, on_request=observe) as db:
        with pytest.raises(OCCredentialError) as error:
            db.usage()
    assert "secret" not in str(error.value)
    assert events[0].error_kind == "credential"


def test_typed_rows_preserve_raw_and_report_failing_index():
    @dataclass
    class Product:
        id: int
        amount: str
    result = SqlExecResult(kind="select", rows=[{"id": 9007199254740993, "amount": "1.00000000000000001"}])
    products = result.map_rows(lambda row: Product(**row))
    assert products == [Product(9007199254740993, "1.00000000000000001")]
    result.map_rows(lambda row: row.clear())
    assert result.rows[0]["id"] == 9007199254740993
    result.rows.append({"unexpected": 1})
    with pytest.raises(RowDecodeError) as failure:
        result.map_rows(lambda row: Product(**row))
    assert failure.value.row == 1 and isinstance(failure.value.__cause__, TypeError)


@pytest.mark.asyncio
async def test_async_rotation_and_cancellation():
    events, headers = [], []
    sequence = iter(["first", "second"])
    async def provider():
        await asyncio.sleep(0)
        return next(sequence)
    def request(req):
        headers.append(req.headers["Authorization"])
        return httpx.Response(200, json={"kind": "select", "rows": []})
    async with AsyncClient(url="https://example.invalid", token_provider=provider,
                           on_request=events.append, transport=httpx.MockTransport(request)) as db:
        await db.sql.run("SELECT 1")
        await db.sql.run("SELECT 1")
    assert headers == ["Bearer first", "Bearer second"]
    assert len(events) == 2

    entered = asyncio.Event()
    async def pending():
        entered.set()
        await asyncio.Event().wait()
        return "never"
    async with AsyncClient(url="https://example.invalid", token_provider=pending, on_request=events.append) as db:
        task = asyncio.create_task(db.sql.run("SELECT 1"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert events[-1].error_kind == "canceled"


def test_from_env_provider_uses_url_without_requiring_static_token(monkeypatch):
    monkeypatch.setenv("OC_URL", "https://example.invalid")
    monkeypatch.delenv("OC_TOKEN", raising=False)
    with Client.from_env(token_provider=lambda: "token") as db:
        assert db.url == "https://example.invalid"


@pytest.mark.asyncio
async def test_async_provider_timeout_does_not_send_request():
    events = []

    async def pending():
        await asyncio.Event().wait()
        return "never"

    def unexpected_request(request):
        pytest.fail("credential timeout must happen before HTTP")

    async with AsyncClient(
        url="https://example.invalid",
        token_provider=pending,
        timeout=0.02,
        on_request=events.append,
        transport=httpx.MockTransport(unexpected_request),
    ) as db:
        with pytest.raises(httpx.TimeoutException):
            await db.sql.run("SELECT 1")
    assert len(events) == 1
    assert events[0].error_kind == "timeout"
