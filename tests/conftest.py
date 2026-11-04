"""Shared test fixtures.

Tests use ``httpx.MockTransport`` to intercept requests at the transport
level - no real socket, no live engine. The ``mock_client`` fixture
returns an ``OriginChain`` whose underlying ``httpx.Client`` routes
through a caller-supplied handler. The handler is the assertion
surface: it inspects the incoming request and returns whatever
``httpx.Response`` the test wants.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Iterator

import httpx
import pytest

from originchain import AsyncOriginChain, OriginChain, _connect


@pytest.fixture(autouse=True)
def _fresh_deprecation_warnings() -> Iterator[None]:
    """Deprecation warnings are one-time per process; reset them so each test
    sees the same behaviour regardless of order."""
    _connect._reset_warnings_for_tests()
    yield
    _connect._reset_warnings_for_tests()


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> OriginChain:
    """Build a sync client with a MockTransport-backed httpx.Client.

    We can't reuse ``OriginChain.__init__`` directly because it builds
    its own httpx.Client. Instead we construct, then swap. ``max_retries
    =0`` keeps tests deterministic - retries would re-call the handler
    and confuse assertion counts."""
    client = OriginChain(
        url="https://test.invalid/v1/tenants/01HX1TESTTENANTXXXXXXXXXX1",
        token="test-bearer",
        max_retries=0,
    )
    client._client = httpx.Client(
        base_url="http://test.invalid",
        transport=httpx.MockTransport(handler),
        headers={
            "Authorization": "Bearer test-bearer",
            "User-Agent": "originchain-python-test/0.3.0",
        },
    )
    return client


class _AwaitingProxy:
    """Run the existing wire-contract assertions against native async methods.

    This adapter is test-only. Async lifecycle, concurrency and cancellation
    tests call AsyncOriginChain directly, without this synchronous facade.
    """

    def __init__(self, target: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._target, self._loop = target, loop

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self._target(*args, **kwargs)
        return self._loop.run_until_complete(result) if inspect.isawaitable(result) else result

    def __getattr__(self, name: str) -> Any:
        if name == "close" and isinstance(self._target, AsyncOriginChain):
            return _AwaitingProxy(self._target.aclose, self._loop)
        value = getattr(self._target, name)
        if callable(value) or name in {"schemas", "rows", "sql", "vector", "fts", "graph", "admin"}:
            return _AwaitingProxy(value, self._loop)
        return value


@pytest.fixture(params=["sync", "async"])
def mock_client(request: Any, monkeypatch: Any) -> Iterator[Any]:
    """Run the same transport contracts for both clients and close each one."""
    clients: list[Any] = []
    loop = asyncio.new_event_loop() if request.param == "async" else None
    cls = AsyncOriginChain if loop is not None else OriginChain
    http_cls = httpx.AsyncClient if loop is not None else httpx.Client
    http_name = "AsyncClient" if loop is not None else "Client"

    def build(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
        monkeypatch.setattr(httpx, http_name, lambda **kw: http_cls(
            **{**kw, "transport": httpx.MockTransport(handler)}
        ))
        client = cls(url="https://test.invalid/v1/tenants/01HX1TESTTENANTXXXXXXXXXX1", token="test-bearer", max_retries=0, verify=False)
        clients.append(client)
        return _AwaitingProxy(client, loop) if loop is not None else client

    try:
        yield build
    finally:
        for client in clients:
            if loop is not None:
                loop.run_until_complete(client.aclose())
            else:
                client.close()
        if loop is not None:
            loop.close()
