"""Native asyncio lifecycle, batching and public namespace contracts.

The shared mock_client fixture separately runs the existing synchronous wire
contract suite against both clients. These tests exercise behavior that a
synchronous facade cannot verify: cooperative cancellation, streaming and
concurrent use of one transport.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import httpx
import pytest

from originchain import AsyncOriginChain, OriginChain, OCServerError


@pytest.fixture
def async_client_factory(monkeypatch):
    real = httpx.AsyncClient
    created = []

    def build(handler, **options):
        def factory(**kwargs):
            client = real(**kwargs, transport=httpx.MockTransport(handler))
            created.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return AsyncOriginChain(
            base_url="https://engine.invalid", bearer="test-bearer", tenant="test",
            verify=False, db_user="test-user", db_key="test-db-key", **options,
        )

    yield build
    assert all(client.is_closed for client in created), "async transport leaked"


@pytest.mark.asyncio
async def test_public_namespace_methods_and_arguments_match():
    with OriginChain(base_url="http://test.invalid", bearer="x", tenant="t", verify=False) as sync:
        async with AsyncOriginChain(
            base_url="http://test.invalid", bearer="x", tenant="t", verify=False,
        ) as asynchronous:
            for name in ("schemas", "rows", "sql", "vector", "fts", "graph", "admin"):
                lhs, rhs = getattr(sync, name), getattr(asynchronous, name)
                public = {n for n in dir(lhs) if not n.startswith("_") and callable(getattr(lhs, n))}
                assert public == {
                    n for n in dir(rhs) if not n.startswith("_") and callable(getattr(rhs, n))
                }, name
                if name == "sql":
                    public.add("__call__")
                for method in public:
                    sync_method, async_method = getattr(lhs, method), getattr(rhs, method)
                    assert inspect.iscoroutinefunction(async_method), f"{name}.{method}"
                    # Async batching additionally accepts AsyncIterable. Public
                    # names, defaults and keyword-only rules must still match.
                    def parameters(fn):
                        return [
                            (p.name, p.kind, p.default)
                            for p in inspect.signature(fn).parameters.values()
                        ]
                    assert parameters(sync_method) == parameters(async_method), f"{name}.{method}"


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous_source", [False, True])
async def test_batch_streams_bounded_chunks_and_counts_server_acceptance(
    async_client_factory, asynchronous_source,
):
    consumed, requests = [], []

    def rows():
        for i in range(5):
            consumed.append(i)
            yield {"id": i}

    async def async_rows():
        for row in rows():
            await asyncio.sleep(0)
            yield row

    def handler(request):
        requests.append(request)
        assert len(consumed) == min(len(requests) * 2, 5)
        assert request.url.path == "/v1/tenants/test/rows/demo.rows/_batch"
        assert dict(request.url.params) == {"expect": "insert"}
        return httpx.Response(200, json={"inserted": [0, 1, 1][len(requests) - 1]})

    async with async_client_factory(handler) as db:
        result = await db.rows.put_batch(
            "demo.rows", async_rows() if asynchronous_source else rows(),
            chunk=2, expect_insert=True, idempotency_key="import",
        )
    assert result == 2
    assert [json.loads(r.content) for r in requests] == [
        [{"id": 0}, {"id": 1}], [{"id": 2}, {"id": 3}], [{"id": 4}],
    ]
    assert [r.headers["idempotency-key"] for r in requests] == [
        "import-c0", "import-c1", "import-c2",
    ]
    assert len({r.headers["x-oc-logical-request-id"] for r in requests}) == 3
    for request in requests:
        assert request.headers["authorization"] == "Bearer test-bearer"
        assert request.headers["x-oc-db-user"] == "test-user"
        assert request.headers["x-oc-db-key"] == "test-db-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [0, -1, True, 1.5, "2"])
async def test_invalid_chunk_does_not_touch_async_input(async_client_factory, chunk):
    class NeverConsumed:
        def __aiter__(self):
            pytest.fail("invalid configuration consumed async input")

    async with async_client_factory(lambda _: pytest.fail("unexpected request")) as db:
        with pytest.raises(ValueError, match="positive integer"):
            await db.rows.put_batch("demo.rows", NeverConsumed(), chunk=chunk)


@pytest.mark.asyncio
async def test_empty_async_batch_and_unique_auto_keys(async_client_factory):
    requests = []

    async def empty():
        for item in []:
            yield item

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"inserted": 1})

    async with async_client_factory(handler) as db:
        assert await db.rows.put_batch("demo.rows", empty()) == 0
        assert requests == []
        for _ in range(2):
            assert await db.rows.put_batch("demo.rows", [{"id": 1}], chunk=1) == 1
    assert requests[0].headers["idempotency-key"] != requests[1].headers["idempotency-key"]


@pytest.mark.asyncio
async def test_batch_failure_stops_consuming_and_does_not_replay(async_client_factory):
    consumed, requests = [], []

    def rows():
        for i in range(10):
            consumed.append(i)
            yield {"id": i}

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200 if len(requests) == 1 else 503,
            json={"inserted": 2, "error": "unavailable"},
            headers={"Retry-After": "0", "X-OC-Request-Id": "0123456789abcdef0123456789ab"},
        )

    async with async_client_factory(handler, max_retries=3) as db:
        with pytest.raises(OCServerError) as caught:
            await db.rows.put_batch("demo.rows", rows(), chunk=2, idempotency_key="import")
    assert consumed == [0, 1, 2, 3]
    assert len(requests) == 2
    assert caught.value.request_id == "0123456789abcdef0123456789ab"
    assert caught.value.logical_request_id == requests[-1].headers["x-oc-logical-request-id"]


@pytest.mark.asyncio
async def test_batch_cancellation_in_flight_stops_later_chunks(async_client_factory):
    entered = asyncio.Event()
    consumed, requests = [], []

    def rows():
        for i in range(10):
            consumed.append(i)
            yield {"id": i}

    async def handler(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    async with async_client_factory(handler) as db:
        task = asyncio.create_task(db.rows.put_batch("demo.rows", rows(), chunk=2))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert consumed == [0, 1]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_batch_cancellation_between_immediate_chunks_is_cooperative(async_client_factory):
    consumed, requests = [], []

    def rows():
        for i in range(10):
            consumed.append(i)
            yield {"id": i}

    def handler(request):
        requests.append(request)
        task = asyncio.current_task()
        asyncio.get_running_loop().call_soon(task.cancel)
        return httpx.Response(200, json={"inserted": 2})

    async with async_client_factory(handler) as db:
        task = asyncio.create_task(db.rows.put_batch("demo.rows", rows(), chunk=2))
        with pytest.raises(asyncio.CancelledError):
            await task
    assert consumed == [0, 1]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_async_source_failure_propagates_after_completed_chunks(async_client_factory):
    requests = []

    async def rows():
        yield {"id": 1}
        yield {"id": 2}
        raise RuntimeError("input stream failed")

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"inserted": 2})

    async with async_client_factory(handler) as db:
        with pytest.raises(RuntimeError, match="input stream failed"):
            await db.rows.put_batch("demo.rows", rows(), chunk=2)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_namespaces_share_identity_and_transport(async_client_factory):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/sql"):
            return httpx.Response(200, json={"kind": "select", "rows": [{"id": 1}]})
        if request.url.path.endswith("/neighbors"):
            return httpx.Response(200, json=["row-1"])
        return httpx.Response(200, json={})

    async with async_client_factory(handler) as db:
        transport = db._client
        assert (await db.sql.query("SELECT * FROM demo.rows")).rows == [{"id": 1}]
        await db.vector.delete_bulk("vectors", ["row-1"])
        await db.fts.index("docs", "text", "row-1", "test")
        assert await db.graph.neighbors_of("demo.rows", "row-1", "related") == ["row-1"]
        await db.admin.get_tenant_config("test")
        assert db._client is transport
    assert len(requests) == 5
    for request in requests:
        assert request.headers["authorization"] == "Bearer test-bearer"
        assert request.headers["x-oc-db-user"] == "test-user"
        assert request.headers["x-oc-db-key"] == "test-db-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["sql", "vector", "fts", "graph", "admin"])
async def test_namespace_writes_never_retry_automatically(async_client_factory, operation):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, json={"error": "busy"}, headers={"Retry-After": "0"})

    async with async_client_factory(handler, max_retries=3) as db:
        calls = {
            "sql": lambda: db.sql.execute("DELETE FROM demo.rows WHERE id='one'"),
            "vector": lambda: db.vector.delete("vectors", "one"),
            "fts": lambda: db.fts.install_stopwords("docs", "text", ["the"]),
            "graph": lambda: db.graph.graphsage("nodes", "features", "follows"),
            "admin": lambda: db.admin.install_tenant_config("test"),
        }
        with pytest.raises(OCServerError):
            await calls[operation]()
    assert len(requests) == 1
    assert requests[0].headers["idempotency-key"]


@pytest.mark.asyncio
async def test_namespace_sql_explicit_retry_preserves_parameters_and_identity(async_client_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            503 if len(requests) == 1 else 200,
            json={"kind": "select", "rows": [{"id": "one"}]}, headers={"Retry-After": "0"},
        )

    async with async_client_factory(handler, max_retries=1) as db:
        result = await db.sql.query(
            "SELECT * FROM demo.rows WHERE id=$1", ["one"],
            retry_safe=True, idempotency_key="query-key",
        )
    assert result.rows == [{"id": "one"}]
    assert len(requests) == 2
    assert [r.headers["x-oc-attempt"] for r in requests] == ["1", "2"]
    assert len({r.headers["x-oc-logical-request-id"] for r in requests}) == 1
    for request in requests:
        assert json.loads(request.content)["params"] == ["one"]
        assert request.headers["idempotency-key"] == "query-key"


@pytest.mark.asyncio
async def test_namespace_read_cancellation_interrupts_backoff(async_client_factory):
    reached = asyncio.Event()
    requests = []

    def handler(request):
        requests.append(request)
        reached.set()
        return httpx.Response(503, headers={"Retry-After": "20"})

    async with async_client_factory(handler, max_retries=3) as db:
        task = asyncio.create_task(db.admin.get_tenant_config("test"))
        await asyncio.wait_for(reached.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_namespaces_run_concurrently_on_one_client(async_client_factory):
    both_entered = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) == 2:
            both_entered.set()
        await both_entered.wait()
        return httpx.Response(200, json=[{"doc_id": request.url.params["q"], "score": 1.0}])

    async with async_client_factory(handler) as db:
        results = await asyncio.wait_for(asyncio.gather(
            db.fts.search("docs", "text", "first"),
            db.fts.search("docs", "text", "second"),
        ), timeout=2)
    assert [result.hits[0].doc_id for result in results] == ["first", "second"]
    assert len({r.headers["x-oc-logical-request-id"] for r in requests}) == 2
