"""Tests for ``client.vector_indexes``: indexes, namespaces, records, jobs and
search, on the sync and the async client.

Every test routes the client through ``httpx.MockTransport``: no socket and
no live engine. Each call's exact method, path, headers and body bytes are
asserted, and so is the mapping of the engine's error envelope. The
``mock_client`` fixture runs each test once per client.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Callable, Iterator, Optional

import httpx
import pytest

from originchain import (
    AsyncOriginChain,
    BatchWriteError,
    OCAuthError,
    OCError,
    OCNotFoundError,
    OCProtocolError,
    OCRateLimitedError,
    OCRecordApiNotEnabledError,
    OCServerError,
    OCValidationError,
    OriginChain,
    UpsertBatchResult,
)
from originchain._vector_indexes import (
    DEFAULT_NAMESPACE,
    UPSERT_MAX_BODY_BYTES,
    UPSERT_MAX_RECORDS,
    _UpsertBatcher,
)

URL = "https://test.invalid/v1/tenants/01HX1TESTTENANTXXXXXXXXXX1"
BASE = "/v1/tenants/01HX1TESTTENANTXXXXXXXXXX1/vector-indexes"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")

INDEX = {
    "index_id": "01J9Z2M4Q5W7Y8X3K6N0P2R4T6",
    "name": "movies",
    "state": "active",
    "dimension": 3,
    "metric": "cosine",
    "score_direction": "higher_is_better",
    "dense_index": "hnsw",
    "sparse": False,
    "text_fields": [],
    "visibility_fields": [],
    "embedding": None,
    "deletion_protection": "disabled",
    "tags": {},
    "identifier_profile": "ascii",
    "compat_family": "vectors",
    "created_at": "2026-09-15T07:00:00.000Z",
}
NAMESPACE = {
    "name": "ns1",
    "namespace_id": "01J9Z2M4Q5W7Y8X3K6N0P2R4T7",
    "state": "active",
    "record_count": 7,
    "pending_records": 0,
    "pending_new_records": 0,
    "dense_ids": 7,
    "sparse_ids": 0,
    "declared_capacity": None,
}


class Engine:
    """A fake engine: answers from a queue (the last answer repeats) and
    records every request it saw."""

    def __init__(self, *answers: tuple[int, Any], headers: Optional[dict[str, str]] = None):
        self.answers = list(answers)
        self.headers = headers or {}
        self.calls: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        index = min(len(self.calls), len(self.answers)) - 1
        status, body = self.answers[index]
        return httpx.Response(status, json=body, headers=self.headers)


def assert_call(req: httpx.Request, method: str, path: str, body: Optional[bytes]) -> None:
    """The exact method, path, headers and body of one request. ``body``
    ``None`` means the request must carry no body."""
    assert req.method == method
    assert req.url.raw_path.decode() == path
    assert req.content == (body or b"")
    assert req.headers["authorization"] == "Bearer test-bearer"
    assert UUID_RE.match(req.headers["x-oc-logical-request-id"])
    assert req.headers["x-oc-attempt"] == "1"
    if body is None:
        assert "content-type" not in req.headers
    else:
        assert req.headers["content-type"] == "application/json"
    if method == "GET":
        assert "idempotency-key" not in req.headers
    else:
        assert HEX32_RE.match(req.headers["idempotency-key"])


def sent(req: httpx.Request) -> Any:
    return json.loads(req.content)


# ── Indexes ───────────────────────────────────────────────────────────


def test_create_index(mock_client: Callable[..., Any]) -> None:
    engine = Engine((201, INDEX))
    out = mock_client(engine).vector_indexes.create_index(
        "movies",
        dimension=3,
        metric="cosine",
        text_fields=[{"name": "body", "analyzer": "standard_en_v1"}],
        visibility_fields=["team"],
        identifier_profile="ascii",
        deletion_protection="enabled",
        tags={"env": "test"},
    )
    assert len(engine.calls) == 1
    assert_call(
        engine.calls[0],
        "POST",
        BASE,
        b'{"name":"movies","dimension":3,"metric":"cosine",'
        b'"text_fields":[{"name":"body","analyzer":"standard_en_v1"}],'
        b'"visibility_fields":["team"],"identifier_profile":"ascii",'
        b'"deletion_protection":"enabled","tags":{"env":"test"}}',
    )
    assert out == INDEX


def test_create_index_minimal_omits_unset_fields(mock_client: Callable[..., Any]) -> None:
    engine = Engine((201, INDEX))
    mock_client(engine).vector_indexes.create_index("m", sparse=True)
    assert_call(engine.calls[0], "POST", BASE, b'{"name":"m","sparse":true}')


def test_describe_index(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, INDEX))
    out = mock_client(engine).vector_indexes.describe_index("movies")
    assert_call(engine.calls[0], "GET", f"{BASE}/movies", None)
    assert out["metric"] == "cosine"


def test_index_path_segment_is_escaped(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, INDEX))
    mock_client(engine).vector_indexes.describe_index("a/b c")
    assert_call(engine.calls[0], "GET", f"{BASE}/a%2Fb%20c", None)


@pytest.mark.parametrize("name", ["", ".", "..", "a\nb"])
def test_unusable_index_name_is_refused_before_any_request(
    mock_client: Callable[..., Any], name: str
) -> None:
    engine = Engine((200, INDEX))
    with pytest.raises(OCValidationError):
        mock_client(engine).vector_indexes.describe_index(name)
    assert engine.calls == []


def test_list_indexes(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"indexes": [INDEX], "next_cursor": "c2"}))
    out = mock_client(engine).vector_indexes.list_indexes(prefix="mov", limit=10, cursor="c1")
    assert_call(
        engine.calls[0], "POST", f"{BASE}/list", b'{"prefix":"mov","limit":10,"cursor":"c1"}'
    )
    assert out["indexes"][0]["name"] == "movies"
    assert out["next_cursor"] == "c2"


def test_list_indexes_without_filter_sends_empty_object(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"indexes": []}))
    out = mock_client(engine).vector_indexes.list_indexes()
    assert_call(engine.calls[0], "POST", f"{BASE}/list", b"{}")
    assert "next_cursor" not in out


def test_configure_index(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {**INDEX, "deletion_protection": "enabled"}))
    out = mock_client(engine).vector_indexes.configure_index(
        "movies", deletion_protection="enabled", tags={"env": ""}
    )
    assert_call(
        engine.calls[0],
        "PATCH",
        f"{BASE}/movies",
        b'{"deletion_protection":"enabled","tags":{"env":""}}',
    )
    assert out["deletion_protection"] == "enabled"


def test_configure_index_needs_a_change(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, INDEX))
    with pytest.raises(ValueError):
        mock_client(engine).vector_indexes.configure_index("movies")
    assert engine.calls == []


def test_delete_index(mock_client: Callable[..., Any]) -> None:
    engine = Engine((202, {**INDEX, "state": "deleting"}))
    out = mock_client(engine).vector_indexes.delete_index("movies")
    assert_call(engine.calls[0], "DELETE", f"{BASE}/movies", None)
    assert out["state"] == "deleting"


def test_index_stats(mock_client: Callable[..., Any]) -> None:
    stats = {
        "index_id": INDEX["index_id"],
        "namespaces": [NAMESPACE],
        "total_record_count": 7,
        "dimension": 3,
        "metric": "cosine",
        "truncated": False,
    }
    engine = Engine((200, stats))
    out = mock_client(engine).vector_indexes.index_stats("movies")
    assert_call(engine.calls[0], "GET", f"{BASE}/movies/stats", None)
    assert out == stats


# ── Namespaces ────────────────────────────────────────────────────────


def test_create_namespace(mock_client: Callable[..., Any]) -> None:
    engine = Engine((201, {**NAMESPACE, "declared_capacity": 250000}))
    out = mock_client(engine).vector_indexes.create_namespace(
        "movies", "ns1", declared_capacity=250000
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/namespaces/create",
        b'{"name":"ns1","declared_capacity":250000}',
    )
    assert out["declared_capacity"] == 250000


def test_describe_namespace(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, NAMESPACE))
    out = mock_client(engine).vector_indexes.describe_namespace("movies", "ns1")
    assert_call(engine.calls[0], "POST", f"{BASE}/movies/namespaces/describe", b'{"name":"ns1"}')
    assert out["record_count"] == 7


def test_list_namespaces(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"namespaces": [NAMESPACE]}))
    out = mock_client(engine).vector_indexes.list_namespaces("movies", prefix="ns", limit=5)
    assert_call(
        engine.calls[0], "POST", f"{BASE}/movies/namespaces/list", b'{"prefix":"ns","limit":5}'
    )
    assert out["namespaces"][0]["record_count"] == 7


def test_configure_namespace_sets_and_removes_capacity(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, NAMESPACE))
    client = mock_client(engine)
    client.vector_indexes.configure_namespace("movies", "ns1", declared_capacity=10)
    client.vector_indexes.configure_namespace("movies", "ns1", declared_capacity=None)
    path = f"{BASE}/movies/namespaces/configure"
    assert_call(engine.calls[0], "POST", path, b'{"name":"ns1","declared_capacity":10}')
    # None is sent as null: it removes the declared capacity.
    assert_call(engine.calls[1], "POST", path, b'{"name":"ns1","declared_capacity":null}')


def test_delete_namespace_sends_confirm(mock_client: Callable[..., Any]) -> None:
    engine = Engine((202, {"namespace_id": "01NS", "state": "deleting", "receipt": {}}))
    out = mock_client(engine).vector_indexes.delete_namespace("movies", "ns1")
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/namespaces/delete",
        b'{"name":"ns1","confirm":"ns1"}',
    )
    assert out["state"] == "deleting"


def test_build_index(mock_client: Callable[..., Any]) -> None:
    engine = Engine((202, {"job_id": "01JOB", "receipt": {}}))
    client = mock_client(engine)
    out = client.vector_indexes.build_index("movies", "ns1", seed=7)
    client.vector_indexes.build_index("movies", "ns1")
    path = f"{BASE}/movies/namespaces/build-index"
    assert_call(engine.calls[0], "POST", path, b'{"namespace":"ns1","seed":7}')
    assert_call(engine.calls[1], "POST", path, b'{"namespace":"ns1"}')
    assert out["job_id"] == "01JOB"


# ── Paging iterators ──────────────────────────────────────────────────


def test_iterators_follow_cursors_to_the_end(mock_client: Callable[..., Any]) -> None:
    client_ix = Engine(
        (200, {"indexes": [INDEX], "next_cursor": "c1"}),
        (200, {"indexes": [], "next_cursor": "c2"}),  # an empty page can carry a cursor
        (200, {"indexes": [{**INDEX, "name": "zeta"}]}),
    )
    names = [ix["name"] for ix in _collect(mock_client(client_ix).vector_indexes.iter_indexes(limit=1))]
    assert names == ["movies", "zeta"]
    assert [sent(c) for c in client_ix.calls] == [
        {"limit": 1},
        {"limit": 1, "cursor": "c1"},
        {"limit": 1, "cursor": "c2"},
    ]

    ids = Engine((200, {"ids": ["a", "b"], "next_cursor": "c1"}), (200, {"ids": ["c"]}))
    got = _collect(mock_client(ids).vector_indexes.iter_ids("movies", "ns1", prefix="d"))
    assert got == ["a", "b", "c"]
    assert [sent(c) for c in ids.calls] == [
        {"namespace": "ns1", "prefix": "d"},
        {"namespace": "ns1", "prefix": "d", "cursor": "c1"},
    ]
    assert {c.url.path for c in ids.calls} == {f"{BASE}/movies/records/list-ids"}

    nss = Engine((200, {"namespaces": [NAMESPACE], "next_cursor": "n1"}), (200, {"namespaces": []}))
    assert len(_collect(mock_client(nss).vector_indexes.iter_namespaces("movies"))) == 1
    assert sent(nss.calls[1]) == {"cursor": "n1"}


def test_iter_records_yields_each_record(mock_client: Callable[..., Any]) -> None:
    engine = Engine(
        (200, {"records": {"a": {"id": "a", "rev": 1}}, "namespace": "ns1", "next_cursor": "f1"}),
        (200, {"records": {"b": {"id": "b", "rev": 2}}, "namespace": "ns1"}),
    )
    flt = {"genre": {"$eq": "drama"}}
    got = _collect(
        mock_client(engine).vector_indexes.iter_records(
            "movies", "ns1", flt, limit=1, include_values=True
        )
    )
    assert [r["id"] for r in got] == ["a", "b"]
    assert [sent(c) for c in engine.calls] == [
        {"namespace": "ns1", "filter_expr": flt, "limit": 1, "include_values": True},
        {"namespace": "ns1", "filter_expr": flt, "limit": 1, "cursor": "f1", "include_values": True},
    ]


def test_a_repeated_cursor_stops_iteration(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"ids": ["a"], "next_cursor": "same"}))
    with pytest.raises(OCProtocolError):
        _collect(mock_client(engine).vector_indexes.iter_ids("movies", "ns1"))
    assert len(engine.calls) == 2


def _collect(iterator: Any) -> list[Any]:
    """Drain a sync iterator, or an async one on a fresh event loop."""
    if hasattr(iterator, "__aiter__"):

        async def drain() -> list[Any]:
            return [item async for item in iterator]

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(drain())
        finally:
            loop.close()
    return list(iterator)


# ── Upsert ────────────────────────────────────────────────────────────


def upsert_echo(
    fail_at: int = 0,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    """Answers each upsert with the number of records it carried."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if fail_at and len(calls) == fail_at:
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "configuration_limit_exceeded",
                        "message": "this index holds the most vectors it may hold",
                        "status": 409,
                        "retryable": False,
                        "details": {"reason": "exact_index_capacity", "limit": 1000},
                    }
                },
            )
        n = len(json.loads(req.content)["records"])
        return httpx.Response(
            200,
            json={"upserted_count": n, "namespace_created": len(calls) == 1, "receipt": {"n": n}},
        )

    return handler, calls


def sent_ids(calls: list[httpx.Request]) -> tuple[list[str], list[int]]:
    ids: list[str] = []
    per_call: list[int] = []
    for req in calls:
        records = json.loads(req.content)["records"]
        per_call.append(len(records))
        ids.extend(r["id"] for r in records)
    return ids, per_call


def test_upsert(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    out = mock_client(handler).vector_indexes.upsert(
        "movies",
        "ns1",
        [
            {"id": "a", "values": [0.5, 1.0, -2.0], "metadata": {"genre": "drama"}},
            {"id": "b", "sparse_values": {"indices": [3, 9], "values": [0.25, 0.5]}},
            {"id": "c", "text": {"body": "a quiet film"}},
        ],
    )
    assert len(calls) == 1
    assert_call(
        calls[0],
        "POST",
        f"{BASE}/movies/records/upsert",
        b'{"namespace":"ns1","records":['
        b'{"id":"a","values":[0.5,1.0,-2.0],"metadata":{"genre":"drama"}},'
        b'{"id":"b","sparse_values":{"indices":[3,9],"values":[0.25,0.5]}},'
        b'{"id":"c","text":{"body":"a quiet film"}}]}',
    )
    assert out == {
        "upserted_count": 3,
        "namespace_created": True,
        "batches": 1,
        "receipts": [{"n": 3}],
    }


def test_upsert_chunks_at_record_cap(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    records = [{"id": f"r{i:04d}", "values": [1.0, 2.0]} for i in range(2500)]
    out = mock_client(handler).vector_indexes.upsert("movies", "ns1", iter(records))
    ids, per_call = sent_ids(calls)
    assert per_call == [UPSERT_MAX_RECORDS, UPSERT_MAX_RECORDS, 500]
    assert ids == [r["id"] for r in records]  # nothing lost, nothing reordered
    assert out["upserted_count"] == 2500
    assert out["batches"] == 3
    assert len(out["receipts"]) == 3
    # Each request is its own write, with its own key.
    assert len({c.headers["idempotency-key"] for c in calls}) == 3


def test_upsert_chunks_at_byte_bound(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    pad = "x" * 300_000  # 6 such records fit under 2,000,000 bytes, 7 do not
    records = [{"id": f"big{i}", "values": [1.0], "metadata": {"pad": pad}} for i in range(10)]
    out = mock_client(handler).vector_indexes.upsert("movies", "ns1", records)
    ids, per_call = sent_ids(calls)
    assert per_call == [6, 4]
    assert all(len(c.content) <= UPSERT_MAX_BODY_BYTES for c in calls)
    assert ids == [r["id"] for r in records]
    assert out["upserted_count"] == 10


def test_upsert_honours_smaller_batch_options(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    records = [{"id": f"r{i}", "values": [1.0]} for i in range(5)]
    mock_client(handler).vector_indexes.upsert(
        "movies", "ns1", records, batch_size=2, max_bytes=100_000
    )
    assert sent_ids(calls)[1] == [2, 2, 1]


def test_upsert_counts_bytes_not_characters() -> None:
    # A 2-byte character: 600,000 of them are 1,200,000 bytes, so two such
    # records cannot share a request although their character count would.
    pad = "é" * 600_000
    batcher = _UpsertBatcher("ns1", UPSERT_MAX_RECORDS, UPSERT_MAX_BODY_BYTES)
    ready = batcher.add({"id": "a", "metadata": {"p": pad}})
    ready += batcher.add({"id": "b", "metadata": {"p": pad}})
    ready += batcher.finish()
    assert [count for _, count, _ in ready] == [1, 1]
    assert all(len(body) <= UPSERT_MAX_BODY_BYTES for _, _, body in ready)


def test_upsert_body_exactly_at_bound_is_one_request(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    probe = _UpsertBatcher("ns1", UPSERT_MAX_RECORDS, UPSERT_MAX_BODY_BYTES)
    probe.add({"id": "edge", "metadata": {"pad": ""}})
    base = len(probe.finish()[0][2])
    fill = UPSERT_MAX_BODY_BYTES - base
    client = mock_client(handler)
    client.vector_indexes.upsert("movies", "ns1", [{"id": "edge", "metadata": {"pad": "y" * fill}}])
    assert len(calls) == 1
    assert len(calls[0].content) == UPSERT_MAX_BODY_BYTES
    # One byte more cannot be sent at all, and is not truncated.
    with pytest.raises(BatchWriteError) as raised:
        client.vector_indexes.upsert(
            "movies", "ns1", [{"id": "edge", "metadata": {"pad": "y" * (fill + 1)}}]
        )
    assert (raised.value.offset, raised.value.count, raised.value.outcome) == (0, 1, "not_sent")
    assert isinstance(raised.value.__cause__, ValueError)
    assert "edge" in str(raised.value.__cause__)
    assert len(calls) == 1


def test_upsert_oversized_record_reports_the_unsent_range(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    records = [
        {"id": "a", "values": [1.0]},
        {"id": "b", "values": [1.0]},
        {"id": "c", "values": [1.0]},
        {"id": "huge", "metadata": {"pad": "z" * UPSERT_MAX_BODY_BYTES}},
    ]
    with pytest.raises(BatchWriteError) as raised:
        mock_client(handler).vector_indexes.upsert("movies", "ns1", records, batch_size=2)
    # a and b were acknowledged; c (held) and huge (refused) were not sent.
    assert sent_ids(calls) == (["a", "b"], [2])
    assert (raised.value.offset, raised.value.count, raised.value.outcome) == (2, 2, "not_sent")


@pytest.mark.parametrize(
    ("record", "cause"),
    [
        ({"id": "a", "values": [float("nan")]}, ValueError),
        ({"id": "a", "values": [float("inf")]}, ValueError),
        ({"id": "a", "values": {1.0}}, TypeError),
        ("not a mapping", TypeError),
    ],
)
def test_upsert_refuses_what_json_cannot_carry(
    mock_client: Callable[..., Any], record: Any, cause: type[Exception]
) -> None:
    handler, calls = upsert_echo()
    with pytest.raises(BatchWriteError) as raised:
        mock_client(handler).vector_indexes.upsert("movies", "ns1", [record])
    assert raised.value.outcome == "not_sent"
    assert isinstance(raised.value.__cause__, cause)
    assert calls == []


def test_upsert_failure_reports_the_unacknowledged_range(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo(fail_at=2)
    records = [{"id": f"r{i}", "values": [1.0]} for i in range(2500)]
    with pytest.raises(BatchWriteError) as raised:
        mock_client(handler).vector_indexes.upsert("movies", "ns1", records)
    err = raised.value
    # Records 0-999 are written; 1000-1999 failed; nothing after was sent.
    assert (err.offset, err.count, err.outcome) == (1000, 1000, "unknown")
    cause = err.__cause__
    assert isinstance(cause, OCError)
    assert cause.status == 409
    assert cause.code == "configuration_limit_exceeded"
    assert cause.reason == "exact_index_capacity"
    assert len(calls) == 2


def test_upsert_batches_yields_each_acknowledged_range(mock_client: Callable[..., Any]) -> None:
    handler, _ = upsert_echo()
    records = [{"id": f"r{i}", "values": [1.0]} for i in range(5)]
    batches = _collect(
        mock_client(handler).vector_indexes.upsert_batches("movies", "ns1", records, batch_size=2)
    )
    assert batches == [
        UpsertBatchResult(offset=0, count=2, upserted_count=2, namespace_created=True, receipt={"n": 2}),
        UpsertBatchResult(offset=2, count=2, upserted_count=2, namespace_created=False, receipt={"n": 2}),
        UpsertBatchResult(offset=4, count=1, upserted_count=1, namespace_created=False, receipt={"n": 1}),
    ]


def test_upsert_answer_without_a_count_is_a_protocol_error(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"receipt": {}}))
    with pytest.raises(BatchWriteError) as raised:
        mock_client(engine).vector_indexes.upsert("movies", "ns1", [{"id": "a", "values": [1.0]}])
    assert raised.value.outcome == "unknown"
    assert isinstance(raised.value.__cause__, OCProtocolError)


def test_upsert_of_nothing_sends_nothing(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    out = mock_client(handler).vector_indexes.upsert("movies", "ns1", [])
    assert out == {"upserted_count": 0, "namespace_created": False, "batches": 0, "receipts": []}
    assert calls == []


@pytest.mark.parametrize(
    "options",
    [{"batch_size": 0}, {"batch_size": 1001}, {"batch_size": True}, {"max_bytes": 2_000_001}, {"max_bytes": 10}],
)
def test_upsert_batch_options_are_checked_before_any_request(
    mock_client: Callable[..., Any], options: dict[str, Any]
) -> None:
    handler, calls = upsert_echo()
    with pytest.raises(ValueError):
        mock_client(handler).vector_indexes.upsert("movies", "ns1", [{"id": "a"}], **options)
    assert calls == []


class ArrayLike:
    """Stands in for an array type with ``tolist`` (numpy, for example)."""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return list(self.values)


def test_array_likes_are_sent_as_json_arrays(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()
    client = mock_client(handler)
    client.vector_indexes.upsert("movies", "ns1", [{"id": "a", "values": ArrayLike([0.5, 1.0])}])
    assert sent(calls[0])["records"][0]["values"] == [0.5, 1.0]
    engine = Engine((200, {"hits": []}))
    mock_client(engine).vector_indexes.search("movies", "ns1", k=1, vector=ArrayLike([0.25]))
    assert sent(engine.calls[0])["dense"] == {"vector": [0.25]}


# ── Fetch, update, delete, count, jobs ────────────────────────────────


def test_fetch(mock_client: Callable[..., Any]) -> None:
    answer = {
        "records": {"a": {"id": "a", "rev": 4, "revision_token": "rv1.x", "values": [0.5, 1.0]}},
        "missing": ["zz"],
        "namespace": "ns1",
        "receipt_position": {"domain": "wal", "value": "1:2"},
    }
    engine = Engine((200, answer))
    out = mock_client(engine).vector_indexes.fetch(
        "movies",
        "ns1",
        ["a", "zz"],
        include_values=True,
        include_sparse=True,
        metadata_fields=["genre"],
        text_fields=["body"],
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/fetch",
        b'{"namespace":"ns1","ids":["a","zz"],"include_values":true,"include_sparse":true,'
        b'"metadata_fields":["genre"],"text_fields":["body"]}',
    )
    assert out == answer


def test_fetch_can_turn_metadata_off(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"records": {}, "missing": []}))
    mock_client(engine).vector_indexes.fetch(
        "movies", DEFAULT_NAMESPACE, ["a"], include_metadata=False
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/fetch",
        b'{"namespace":"__default__","ids":["a"],"include_metadata":false}',
    )


def test_a_string_is_not_a_list_of_ids(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {}))
    with pytest.raises(TypeError):
        mock_client(engine).vector_indexes.fetch("movies", "ns1", "abc")
    with pytest.raises(TypeError):
        mock_client(engine).vector_indexes.delete("movies", "ns1", "abc")
    assert engine.calls == []


def test_fetch_by_filter(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"records": {}, "namespace": "ns1", "next_cursor": "f2"}))
    client = mock_client(engine)
    out = client.vector_indexes.fetch_by_filter(
        "movies", "ns1", {"year": {"$gt": 1999}}, limit=50, cursor="f1", include_metadata=False
    )
    client.vector_indexes.fetch_by_filter("movies", "ns1", filter={"genre": "drama"})
    path = f"{BASE}/movies/records/fetch-by-filter"
    assert_call(
        engine.calls[0],
        "POST",
        path,
        b'{"namespace":"ns1","filter_expr":{"year":{"$gt":1999}},"limit":50,"cursor":"f1",'
        b'"include_metadata":false}',
    )
    assert_call(engine.calls[1], "POST", path, b'{"namespace":"ns1","filter":{"genre":"drama"}}')
    assert out["next_cursor"] == "f2"


@pytest.mark.parametrize(
    "call",
    [
        lambda vi: vi.fetch_by_filter("movies", "ns1"),
        lambda vi: vi.fetch_by_filter("movies", "ns1", {"a": {"$eq": 1}}, filter={"a": 1}),
        lambda vi: vi.delete_by_filter("movies", "ns1"),
        lambda vi: vi.update_by_filter("movies", "ns1", metadata_merge={"a": 1}),
        lambda vi: vi.update_by_filter("movies", "ns1", {"a": {"$eq": 1}}),
        lambda vi: vi.count("movies", "ns1", {"a": {"$eq": 1}}, filter={"a": 1}),
        lambda vi: vi.update("movies", "ns1", "a"),
    ],
)
def test_bad_selectors_are_refused_before_any_request(
    mock_client: Callable[..., Any], call: Callable[[Any], Any]
) -> None:
    engine = Engine((200, {}))
    with pytest.raises(ValueError):
        call(mock_client(engine).vector_indexes)
    assert engine.calls == []


def test_list_ids(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"ids": ["a", "b"], "next_cursor": "l2"}))
    out = mock_client(engine).vector_indexes.list_ids(
        "movies", "ns1", prefix="doc-", limit=2, cursor="l1"
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/list-ids",
        b'{"namespace":"ns1","prefix":"doc-","limit":2,"cursor":"l1"}',
    )
    assert out["ids"] == ["a", "b"]


def test_update_by_id(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"receipt": {"records": [{"id": "a", "rev": 5}]}}))
    out = mock_client(engine).vector_indexes.update(
        "movies",
        "ns1",
        "a",
        values=[0.1, 0.2, 0.3],
        sparse_values={"indices": [1], "values": [0.5]},
        metadata_merge={"genre": "comedy"},
        metadata_remove=["draft"],
        text_set={"body": "new"},
        text_remove=["title"],
        if_revision_token="rv1.abc",
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/update",
        b'{"namespace":"ns1","id":"a","values":[0.1,0.2,0.3],'
        b'"sparse_values":{"indices":[1],"values":[0.5]},"metadata_merge":{"genre":"comedy"},'
        b'"metadata_remove":["draft"],"text_set":{"body":"new"},"text_remove":["title"],'
        b'"if_revision_token":"rv1.abc"}',
    )
    assert out["receipt"]["records"][0]["rev"] == 5


def test_update_replace_metadata(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"receipt": {}}))
    mock_client(engine).vector_indexes.update(
        "movies", "ns1", "a", metadata_replace={"genre": "drama"}
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/update",
        b'{"namespace":"ns1","id":"a","metadata_replace":{"genre":"drama"}}',
    )


def test_update_by_filter(mock_client: Callable[..., Any]) -> None:
    engine = Engine((202, {"job_id": "01JOB", "receipt": {}}))
    client = mock_client(engine)
    out = client.vector_indexes.update_by_filter(
        "movies", "ns1", {"genre": {"$eq": "drama"}}, metadata_merge={"seen": True}
    )
    client.vector_indexes.update_by_filter(
        "movies", "ns1", filter={"genre": "drama"}, metadata_remove=["seen"], dry_run=True
    )
    path = f"{BASE}/movies/records/update"
    assert_call(
        engine.calls[0],
        "POST",
        path,
        b'{"namespace":"ns1","filter_expr":{"genre":{"$eq":"drama"}},"metadata_merge":{"seen":true}}',
    )
    assert_call(
        engine.calls[1],
        "POST",
        path,
        b'{"namespace":"ns1","filter":{"genre":"drama"},"metadata_remove":["seen"],"dry_run":true}',
    )
    assert out["job_id"] == "01JOB"


def test_delete_by_ids(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"deleted": 1, "missing": 1, "receipt": {}}))
    out = mock_client(engine).vector_indexes.delete("movies", "ns1", ["a", "zz"])
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/delete",
        b'{"namespace":"ns1","ids":["a","zz"]}',
    )
    assert (out["deleted"], out["missing"]) == (1, 1)


def test_delete_by_filter_is_a_job(mock_client: Callable[..., Any]) -> None:
    engine = Engine((202, {"job_id": "01JOB", "receipt": {}}))
    out = mock_client(engine).vector_indexes.delete_by_filter(
        "movies", "ns1", {"genre": {"$eq": "drama"}}
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/delete",
        b'{"namespace":"ns1","filter_expr":{"genre":{"$eq":"drama"}}}',
    )
    assert out["job_id"] == "01JOB"


def test_delete_by_filter_dry_run(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"dry_run": True, "matched_records": 12, "sample_ids": ["a"]}))
    out = mock_client(engine).vector_indexes.delete_by_filter(
        "movies", "ns1", filter={"genre": "drama"}, dry_run=True
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/records/delete",
        b'{"namespace":"ns1","filter":{"genre":"drama"},"dry_run":true}',
    )
    assert out["matched_records"] == 12
    assert "job_id" not in out


def test_delete_all_sends_confirm(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"receipt": {}}))
    client = mock_client(engine)
    client.vector_indexes.delete_all("movies", "ns1")
    client.vector_indexes.delete_all("movies", "ns1", dry_run=True)
    path = f"{BASE}/movies/records/delete"
    assert_call(engine.calls[0], "POST", path, b'{"namespace":"ns1","all":true,"confirm":"ns1"}')
    assert_call(
        engine.calls[1],
        "POST",
        path,
        b'{"namespace":"ns1","all":true,"confirm":"ns1","dry_run":true}',
    )


def test_count(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, {"count": 3, "namespace": "ns1"}))
    client = mock_client(engine)
    out = client.vector_indexes.count("movies", "ns1")
    client.vector_indexes.count("movies", "ns1", {"genre": {"$in": ["a", "b"]}})
    path = f"{BASE}/movies/records/count"
    assert_call(engine.calls[0], "POST", path, b'{"namespace":"ns1"}')
    assert_call(
        engine.calls[1],
        "POST",
        path,
        b'{"namespace":"ns1","filter_expr":{"genre":{"$in":["a","b"]}}}',
    )
    assert out["count"] == 3


def test_get_and_cancel_job(mock_client: Callable[..., Any]) -> None:
    job = {"job_id": "01JOB", "kind": "delete_by_filter", "state": "running", "progress": {}}
    engine = Engine((200, job), (202, {}))
    client = mock_client(engine)
    out = client.vector_indexes.get_job("movies", "01JOB")
    client.vector_indexes.cancel_job("movies", "01JOB")
    assert_call(engine.calls[0], "GET", f"{BASE}/movies/jobs/01JOB", None)
    assert_call(engine.calls[1], "POST", f"{BASE}/movies/jobs/01JOB/cancel", None)
    assert out == job


# ── Search ────────────────────────────────────────────────────────────

SEARCH = {
    "index_id": "01IX",
    "namespace": "ns1",
    "mode": "semantic",
    "ranking_method": "dense_similarity",
    "k": 2,
    "returned": 1,
    "completeness": {"value": "ann_full"},
    "hits": [{"id": "a", "score": 0.91, "rank": 1, "metadata": {"genre": "drama"}}],
}


def test_search_semantic_dense(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, SEARCH))
    out = mock_client(engine).vector_indexes.search(
        "movies",
        "ns1",
        k=2,
        vector=[0.5, 1.0, -2.0],
        quality="exact",
        filter_expr={"genre": {"$eq": "drama"}},
        include_values=True,
        include_metadata=True,
        metadata_fields=["genre"],
        include_components=False,
        require_complete=True,
        timeout_ms=500,
    )
    assert_call(
        engine.calls[0],
        "POST",
        f"{BASE}/movies/search",
        b'{"namespace":"ns1","mode":"semantic","k":2,'
        b'"dense":{"vector":[0.5,1.0,-2.0],"quality":"exact"},'
        b'"filter_expr":{"genre":{"$eq":"drama"}},'
        b'"include_values":true,"include_metadata":true,"metadata_fields":["genre"],'
        b'"include_components":false,"require_complete":true,"timeout_ms":500}',
    )
    assert out["hits"][0]["id"] == "a"


def test_search_minimal_and_by_record_id(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, SEARCH))
    client = mock_client(engine)
    client.vector_indexes.search("movies", "ns1", k=5, vector=[1.0])
    client.vector_indexes.search("movies", "ns1", k=5, record_id="a", filter={"genre": "drama"})
    path = f"{BASE}/movies/search"
    assert_call(
        engine.calls[0],
        "POST",
        path,
        b'{"namespace":"ns1","mode":"semantic","k":5,"dense":{"vector":[1.0]}}',
    )
    assert_call(
        engine.calls[1],
        "POST",
        path,
        b'{"namespace":"ns1","mode":"semantic","k":5,"dense":{"record_id":"a"},'
        b'"filter":{"genre":"drama"}}',
    )


def test_search_sparse_lexical_and_hybrid(mock_client: Callable[..., Any]) -> None:
    engine = Engine((200, SEARCH))
    vi = mock_client(engine).vector_indexes
    sparse = {"indices": [3, 9], "values": [0.2, 0.5], "scorer": "dot"}
    lexical = {"field": "body", "query": "quiet film", "match": "phrase"}
    vi.search("movies", "ns1", k=3, sparse=sparse)
    vi.search("movies", "ns1", k=3, lexical=lexical, text_fields=["body"])
    vi.search(
        "movies",
        "ns1",
        k=3,
        dense={"vector": [1.0], "candidates": 80},
        sparse=sparse,
        lexical=lexical,
        fusion={"method": "rrf", "rrf_k": 20},
    )
    vi.search("movies", "ns1", k=3, vector=[1.0], lexical=lexical)
    vi.search("movies", "ns1", k=3, vector=[1.0], mode="dense_with_text_filter")
    bodies = [sent(c) for c in engine.calls]
    assert bodies[0] == {"namespace": "ns1", "mode": "semantic", "k": 3, "sparse": sparse}
    assert bodies[1] == {
        "namespace": "ns1", "mode": "lexical", "k": 3, "lexical": lexical, "text_fields": ["body"],
    }
    assert bodies[2] == {
        "namespace": "ns1",
        "mode": "hybrid",
        "k": 3,
        "dense": {"vector": [1.0], "candidates": 80},
        "sparse": sparse,
        "lexical": lexical,
        "fusion": {"method": "rrf", "rrf_k": 20},
    }
    # Without `fusion`, a hybrid search is fused by reciprocal rank on the engine.
    assert bodies[3] == {
        "namespace": "ns1", "mode": "hybrid", "k": 3, "dense": {"vector": [1.0]}, "lexical": lexical,
    }
    assert bodies[4]["mode"] == "dense_with_text_filter"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"k": 5},
        {"k": 5, "vector": [1.0], "record_id": "a"},
        {"k": 5, "dense": {"vector": [1.0]}, "vector": [1.0]},
        {"k": 5, "dense": {"vector": [1.0]}, "quality": "exact"},
        {"k": 5, "quality": "exact", "sparse": {"indices": [1], "values": [1.0]}},
        {"k": 0, "vector": [1.0]},
        {"k": True, "vector": [1.0]},
        {"k": "5", "vector": [1.0]},
        {"k": 5, "vector": [1.0], "filter_expr": {"a": {"$eq": 1}}, "filter": {"a": 1}},
        {"k": 5, "vector": [float("nan")]},
    ],
)
def test_search_refuses_bad_queries_before_any_request(
    mock_client: Callable[..., Any], kwargs: dict[str, Any]
) -> None:
    engine = Engine((200, SEARCH))
    with pytest.raises(ValueError):
        mock_client(engine).vector_indexes.search("movies", "ns1", **kwargs)
    assert engine.calls == []


# ── Errors ────────────────────────────────────────────────────────────


def envelope(
    code: str, status: int, retryable: bool, details: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": f"message for {code}",
        "status": status,
        "retryable": retryable,
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


ERROR_CASES = [
    (400, "invalid_argument", False, {"reason": "k_out_of_range", "limit": 10000}, OCValidationError),
    (400, "unknown_request_field", False, {"unknown": ["/dense/bogus"]}, OCValidationError),
    (401, "unauthorized", False, None, OCAuthError),
    (403, "key_scope_insufficient", False, None, OCAuthError),
    (404, "index_not_found", False, None, OCNotFoundError),
    (404, "record_not_found", False, None, OCNotFoundError),
    (409, "index_already_exists", False, {"reason": "index_already_exists"}, OCError),
    (409, "index_capability_missing", False, {"reason": "sparse_not_declared"}, OCError),
    (409, "record_write_conflict", True, None, OCError),
    (413, "request_too_large", False, None, OCError),
    (422, "filtered_search_incomplete", False, None, OCError),
    (429, "overloaded", True, None, OCRateLimitedError),
    (501, "not_implemented", False, {"reason": "record_api_not_enabled"}, OCRecordApiNotEnabledError),
    (501, "not_implemented", False, {"reason": "ivf_pq_index_rebuild"}, OCServerError),
    (503, "record_api_restore_pending", True, None, OCServerError),
]


@pytest.mark.parametrize(("status", "code", "retryable", "details", "exc"), ERROR_CASES)
def test_error_mapping(
    mock_client: Callable[..., Any],
    status: int,
    code: str,
    retryable: bool,
    details: Optional[dict[str, Any]],
    exc: type[OCError],
) -> None:
    request_id = "0190b3e2-8f4a-7c1d-b5e6-0123456789ab"
    engine = Engine(
        (status, envelope(code, status, retryable, details)),
        headers={"X-OC-Request-Id": request_id, "Retry-After": "7"},
    )
    with pytest.raises(OCError) as raised:
        mock_client(engine).vector_indexes.search("movies", "ns1", k=5, vector=[1.0])
    err = raised.value
    assert type(err) is exc
    assert err.status == status
    assert err.code == code
    assert err.retryable is retryable
    assert err.details == details
    assert err.reason == (details or {}).get("reason")
    assert str(err) == f"message for {code}"
    assert err.request_id == request_id
    assert err.logical_request_id == engine.calls[0].headers["x-oc-logical-request-id"]
    if isinstance(err, OCRateLimitedError):
        assert err.retry_after == 7.0
    assert len(engine.calls) == 1  # no automatic retries by default


def test_record_api_not_enabled_is_a_server_error(mock_client: Callable[..., Any]) -> None:
    engine = Engine((501, envelope("not_implemented", 501, False, {"reason": "record_api_not_enabled"})))
    with pytest.raises(OCServerError) as raised:
        mock_client(engine).vector_indexes.list_indexes()
    assert isinstance(raised.value, OCRecordApiNotEnabledError)


def test_flat_error_body_leaves_envelope_fields_unset(mock_client: Callable[..., Any]) -> None:
    # Other routes answer {"error": "<text>"}; that keeps working.
    engine = Engine((400, {"error": "bad thing"}))
    with pytest.raises(OCValidationError) as raised:
        mock_client(engine).vector_indexes.describe_index("movies")
    err = raised.value
    assert str(err) == "bad thing"
    assert (err.code, err.reason, err.details, err.retryable) == (None, None, None, None)


def test_envelope_without_message_uses_its_code(mock_client: Callable[..., Any]) -> None:
    engine = Engine((404, {"error": {"code": "job_not_found"}}))
    with pytest.raises(OCNotFoundError) as raised:
        mock_client(engine).vector_indexes.get_job("movies", "01JOB")
    assert str(raised.value) == "job_not_found"
    assert raised.value.code == "job_not_found"


# ── Retries ───────────────────────────────────────────────────────────


def _retrying(engine: Engine) -> OriginChain:
    return OriginChain(
        url=URL, token="test-bearer", max_retries=1, transport=httpx.MockTransport(engine)
    )


def test_reads_are_retried_when_retries_are_on(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("originchain.client.time.sleep", slept.append)
    engine = Engine(
        (429, envelope("overloaded", 429, True)), (200, SEARCH), headers={"Retry-After": "3"}
    )
    with _retrying(engine) as client:
        out = client.vector_indexes.search("movies", "ns1", k=5, vector=[1.0])
    assert out["returned"] == 1
    assert slept == [3.0]
    assert [c.headers["x-oc-attempt"] for c in engine.calls] == ["1", "2"]
    assert engine.calls[0].content == engine.calls[1].content
    ids = [c.headers["x-oc-logical-request-id"] for c in engine.calls]
    assert ids[0] == ids[1]


@pytest.mark.parametrize(
    "call",
    [
        lambda vi: vi.upsert("movies", "ns1", [{"id": "a", "values": [1.0]}]),
        lambda vi: vi.delete("movies", "ns1", ["a"]),
        lambda vi: vi.delete_by_filter("movies", "ns1", {"a": {"$eq": 1}}),
        lambda vi: vi.update("movies", "ns1", "a", metadata_merge={"b": 1}),
        lambda vi: vi.create_index("movies", dimension=3),
        lambda vi: vi.delete_index("movies"),
        lambda vi: vi.cancel_job("movies", "01JOB"),
    ],
)
def test_writes_are_never_sent_twice(
    monkeypatch: pytest.MonkeyPatch, call: Callable[[Any], Any]
) -> None:
    monkeypatch.setattr("originchain.client.time.sleep", lambda _: None)
    engine = Engine((503, envelope("unavailable", 503, True)), (200, {"upserted_count": 1}))
    with _retrying(engine) as client:
        with pytest.raises((OCServerError, BatchWriteError)):
            call(client.vector_indexes)
    assert len(engine.calls) == 1


# ── Async client ──────────────────────────────────────────────────────


def run_async(
    engine: Callable[[httpx.Request], httpx.Response], use: Callable[[AsyncOriginChain], Any]
) -> Any:
    async def go() -> Any:
        async with AsyncOriginChain(
            url=URL, token="test-bearer", max_retries=0, transport=httpx.MockTransport(engine)
        ) as db:
            return await use(db)

    return asyncio.run(go())


def test_async_upsert_accepts_an_async_source_and_reports_progress() -> None:
    handler, calls = upsert_echo(fail_at=3)

    async def records() -> AsyncIterator[dict[str, Any]]:
        for i in range(2500):
            yield {"id": f"r{i}", "values": [1.0]}

    async def use(db: AsyncOriginChain) -> None:
        await db.vector_indexes.upsert("movies", "ns1", records())

    with pytest.raises(BatchWriteError) as raised:
        run_async(handler, use)
    err = raised.value
    assert (err.offset, err.count, err.outcome) == (2000, 500, "unknown")
    assert isinstance(err.__cause__, OCError)
    assert err.__cause__.reason == "exact_index_capacity"
    assert sent_ids(calls)[1] == [1000, 1000, 500]


def test_async_upsert_batches_and_iterators() -> None:
    handler, calls = upsert_echo()

    def engine(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/records/list-ids"):
            cursor = json.loads(req.content).get("cursor")
            page = {"ids": ["a"], "next_cursor": "c1"} if cursor is None else {"ids": ["b"]}
            return httpx.Response(200, json=page)
        return handler(req)

    async def use(db: AsyncOriginChain) -> tuple[list[UpsertBatchResult], list[str]]:
        batches = [
            b
            async for b in db.vector_indexes.upsert_batches(
                "movies", "ns1", ({"id": f"r{i}"} for i in range(3)), batch_size=2
            )
        ]
        ids = [i async for i in db.vector_indexes.iter_ids("movies", "ns1")]
        return batches, ids

    batches, ids = run_async(engine, use)
    assert [(b.offset, b.count) for b in batches] == [(0, 2), (2, 1)]
    assert ids == ["a", "b"]


def test_async_source_failure_is_not_sent() -> None:
    handler, calls = upsert_echo()

    async def records() -> AsyncIterator[dict[str, Any]]:
        yield {"id": "a"}
        raise RuntimeError("source broke")

    async def use(db: AsyncOriginChain) -> None:
        await db.vector_indexes.upsert("movies", "ns1", records())

    with pytest.raises(BatchWriteError) as raised:
        run_async(handler, use)
    assert (raised.value.offset, raised.value.count, raised.value.outcome) == (0, 1, "not_sent")
    assert calls == []


def test_sync_source_failure_is_not_sent(mock_client: Callable[..., Any]) -> None:
    handler, calls = upsert_echo()

    def records() -> Iterator[dict[str, Any]]:
        yield {"id": "a"}
        yield {"id": "b"}
        raise RuntimeError("source broke")

    with pytest.raises(BatchWriteError) as raised:
        mock_client(handler).vector_indexes.upsert("movies", "ns1", records(), batch_size=1)
    # a and b were acknowledged one by one; the source failed after them.
    assert sent_ids(calls) == (["a", "b"], [1, 1])
    assert (raised.value.offset, raised.value.count, raised.value.outcome) == (2, 0, "not_sent")


def test_both_clients_expose_the_same_methods() -> None:
    from originchain._vector_indexes import _AsyncVectorIndexesNamespace, _VectorIndexesNamespace

    public = lambda cls: {n for n in vars(cls) if not n.startswith("_")}  # noqa: E731
    assert public(_VectorIndexesNamespace) == public(_AsyncVectorIndexesNamespace)
    assert len(public(_VectorIndexesNamespace)) == 30
