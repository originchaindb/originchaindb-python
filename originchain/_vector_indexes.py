"""``client.vector_indexes``: record operations on vector indexes.

An index holds namespaces, and a namespace holds records. A record has an
``id`` and any of a dense vector (``values``), a sparse vector
(``sparse_values``), ``metadata`` and declared ``text`` fields. Every method
takes the index by its name or its ``index_id``. Record calls always name a
namespace; ``"__default__"`` is the default one.

The routes are ``$OC_URL/vector-indexes/...``. They are separate from the
per-table ``client.vector`` calls (``$OC_URL/vector/<table>/...``), which keep
working unchanged; the two never share data.

The request bodies are built by the plain functions at the top of this module,
so the sync and async clients send byte-identical requests.

Errors are the client's usual :class:`~originchain.errors.OCError` classes.
Branch on ``err.code`` plus ``err.reason`` (the engine's
``error.details.reason``), never on the HTTP status alone. A database where
these operations are not switched on answers every call with
:class:`~originchain.errors.OCRecordApiNotEnabledError`.

Retries: ``max_retries`` applies to the read operations (describe, list,
fetch, count, statistics, search, job status), which are safe to send again.
Writes are never sent twice by this client.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterable,
    AsyncIterator,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ._paths import segment
from .errors import OCProtocolError
from .iteration import BatchWriteError

if TYPE_CHECKING:
    from .async_client import AsyncOriginChain
    from .client import OriginChain

#: The engine's cap on records in one upsert request.
UPSERT_MAX_RECORDS = 1000
#: The engine's bound on one upsert request body, in bytes.
UPSERT_MAX_BODY_BYTES = 2_000_000
#: The default namespace.
DEFAULT_NAMESPACE = "__default__"

_JSON = {"Content-Type": "application/json"}

# (method, path relative to OC_URL, body bytes or None, safe to send again)
_Call = Tuple[str, str, Optional[bytes], bool]


@dataclass(frozen=True)
class UpsertBatchResult:
    """One acknowledged upsert request. ``offset`` and ``count`` name its
    range in the caller's input; ``receipt`` is the engine's write receipt."""

    offset: int
    count: int
    upserted_count: int
    namespace_created: bool
    receipt: Any


# ── Encoding ──────────────────────────────────────────────────────────


def _json_default(value: Any) -> Any:
    # Array-likes and their scalars (for example numpy) expose ``tolist``.
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _encode(obj: Any) -> bytes:
    # Compact, and strict about NaN / Infinity: they are not JSON and the
    # engine refuses them, so fail here rather than send something else.
    return _json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_json_default
    ).encode("utf-8")


def _put(body: dict[str, Any], **optional: Any) -> dict[str, Any]:
    for key, value in optional.items():
        if value is not None:
            body[key] = value
    return body


def _seq(values: Any) -> Any:
    if values is None:
        return None
    tolist = getattr(values, "tolist", None)
    return tolist() if callable(tolist) else list(values)


def _strings(values: Optional[Sequence[str]], name: str) -> Optional[List[str]]:
    if values is None:
        return None
    if isinstance(values, str):
        raise TypeError(f"{name} must be a sequence of strings, not a string")
    return list(values)


def _one_filter(filter_expr: Any, filter: Any, *, required: bool) -> dict[str, Any]:
    if filter_expr is not None and filter is not None:
        raise ValueError("pass at most one of filter_expr and filter")
    if required and filter_expr is None and filter is None:
        raise ValueError("filter_expr or filter is required")
    if filter_expr is not None:
        return {"filter_expr": filter_expr}
    if filter is not None:
        return {"filter": dict(filter)}
    return {}


def _indexes() -> str:
    return "vector-indexes"


def _index(index: str, suffix: str = "") -> str:
    return f"vector-indexes/{segment(index)}{suffix}"


def _post(path: str, body: Mapping[str, Any], *, read: bool) -> _Call:
    return "POST", path, _encode(body), read


# ── Indexes ───────────────────────────────────────────────────────────


def _create_index(
    name: str,
    dimension: Optional[int],
    metric: Optional[str],
    sparse: Optional[bool],
    text_fields: Optional[Sequence[Mapping[str, str]]],
    visibility_fields: Optional[Sequence[str]],
    identifier_profile: Optional[str],
    deletion_protection: Optional[str],
    tags: Optional[Mapping[str, str]],
) -> _Call:
    body = _put(
        {"name": name},
        dimension=dimension,
        metric=metric,
        sparse=sparse,
        text_fields=[dict(f) for f in text_fields] if text_fields is not None else None,
        visibility_fields=_strings(visibility_fields, "visibility_fields"),
        identifier_profile=identifier_profile,
        deletion_protection=deletion_protection,
        tags=dict(tags) if tags is not None else None,
    )
    return _post(_indexes(), body, read=False)


def _describe_index(index: str) -> _Call:
    return "GET", _index(index), None, True


def _list_indexes(
    name: Optional[str], prefix: Optional[str], limit: Optional[int], cursor: Optional[str]
) -> _Call:
    body = _put({}, name=name, prefix=prefix, limit=limit, cursor=cursor)
    return _post(_indexes() + "/list", body, read=True)


def _configure_index(
    index: str, deletion_protection: Optional[str], tags: Optional[Mapping[str, str]]
) -> _Call:
    if deletion_protection is None and tags is None:
        raise ValueError("configure_index needs deletion_protection or tags")
    body = _put(
        {},
        deletion_protection=deletion_protection,
        tags=dict(tags) if tags is not None else None,
    )
    return "PATCH", _index(index), _encode(body), False


def _delete_index(index: str) -> _Call:
    return "DELETE", _index(index), None, False


def _index_stats(index: str) -> _Call:
    return "GET", _index(index, "/stats"), None, True


# ── Namespaces ────────────────────────────────────────────────────────


def _create_namespace(index: str, name: str, declared_capacity: Optional[int]) -> _Call:
    body = _put({"name": name}, declared_capacity=declared_capacity)
    return _post(_index(index, "/namespaces/create"), body, read=False)


def _describe_namespace(index: str, name: str) -> _Call:
    return _post(_index(index, "/namespaces/describe"), {"name": name}, read=True)


def _list_namespaces(
    index: str, prefix: Optional[str], limit: Optional[int], cursor: Optional[str]
) -> _Call:
    body = _put({}, prefix=prefix, limit=limit, cursor=cursor)
    return _post(_index(index, "/namespaces/list"), body, read=True)


def _configure_namespace(index: str, name: str, declared_capacity: Optional[int]) -> _Call:
    # ``None`` is sent as JSON null: it removes the declared capacity.
    body = {"name": name, "declared_capacity": declared_capacity}
    return _post(_index(index, "/namespaces/configure"), body, read=False)


def _delete_namespace(index: str, name: str) -> _Call:
    body = {"name": name, "confirm": name}
    return _post(_index(index, "/namespaces/delete"), body, read=False)


def _build_index(index: str, namespace: str, seed: Optional[int]) -> _Call:
    body = _put({"namespace": namespace}, seed=seed)
    return _post(_index(index, "/namespaces/build-index"), body, read=False)


# ── Records ───────────────────────────────────────────────────────────


def _projection(
    include_values: Optional[bool],
    include_sparse: Optional[bool],
    include_metadata: Optional[bool],
    metadata_fields: Optional[Sequence[str]],
    text_fields: Optional[Sequence[str]],
) -> dict[str, Any]:
    return _put(
        {},
        include_values=include_values,
        include_sparse=include_sparse,
        include_metadata=include_metadata,
        metadata_fields=_strings(metadata_fields, "metadata_fields"),
        text_fields=_strings(text_fields, "text_fields"),
    )


def _fetch(index: str, namespace: str, ids: Sequence[str], projection: dict[str, Any]) -> _Call:
    body = {"namespace": namespace, "ids": _strings(ids, "ids"), **projection}
    return _post(_index(index, "/records/fetch"), body, read=True)


def _fetch_by_filter(
    index: str,
    namespace: str,
    selector: dict[str, Any],
    limit: Optional[int],
    cursor: Optional[str],
    projection: dict[str, Any],
) -> _Call:
    body = _put({"namespace": namespace, **selector}, limit=limit, cursor=cursor)
    body.update(projection)
    return _post(_index(index, "/records/fetch-by-filter"), body, read=True)


def _list_ids(
    index: str, namespace: str, prefix: Optional[str], limit: Optional[int], cursor: Optional[str]
) -> _Call:
    body = _put({"namespace": namespace}, prefix=prefix, limit=limit, cursor=cursor)
    return _post(_index(index, "/records/list-ids"), body, read=True)


def _update(
    index: str,
    namespace: str,
    id: str,
    values: Any,
    sparse_values: Optional[Mapping[str, Any]],
    metadata_merge: Optional[Mapping[str, Any]],
    metadata_replace: Optional[Mapping[str, Any]],
    metadata_remove: Optional[Sequence[str]],
    text_set: Optional[Mapping[str, str]],
    text_remove: Optional[Sequence[str]],
    if_revision_token: Optional[str],
) -> _Call:
    if not isinstance(id, str):
        raise TypeError("id must be a string")
    changes = _put(
        {},
        values=_seq(values),
        sparse_values=dict(sparse_values) if sparse_values is not None else None,
        metadata_merge=dict(metadata_merge) if metadata_merge is not None else None,
        metadata_replace=dict(metadata_replace) if metadata_replace is not None else None,
        metadata_remove=_strings(metadata_remove, "metadata_remove"),
        text_set=dict(text_set) if text_set is not None else None,
        text_remove=_strings(text_remove, "text_remove"),
    )
    if not changes:
        raise ValueError("update needs at least one change")
    body = _put({"namespace": namespace, "id": id, **changes}, if_revision_token=if_revision_token)
    return _post(_index(index, "/records/update"), body, read=False)


def _update_by_filter(
    index: str,
    namespace: str,
    selector: dict[str, Any],
    metadata_merge: Optional[Mapping[str, Any]],
    metadata_remove: Optional[Sequence[str]],
    dry_run: bool,
) -> _Call:
    changes = _put(
        {},
        metadata_merge=dict(metadata_merge) if metadata_merge is not None else None,
        metadata_remove=_strings(metadata_remove, "metadata_remove"),
    )
    if not changes:
        raise ValueError("update_by_filter needs metadata_merge or metadata_remove")
    body: dict[str, Any] = {"namespace": namespace, **selector, **changes}
    if dry_run:
        body["dry_run"] = True
    return _post(_index(index, "/records/update"), body, read=False)


def _delete_ids(index: str, namespace: str, ids: Sequence[str]) -> _Call:
    body = {"namespace": namespace, "ids": _strings(ids, "ids")}
    return _post(_index(index, "/records/delete"), body, read=False)


def _delete_by_filter(
    index: str, namespace: str, selector: dict[str, Any], dry_run: bool
) -> _Call:
    body: dict[str, Any] = {"namespace": namespace, **selector}
    if dry_run:
        body["dry_run"] = True
    return _post(_index(index, "/records/delete"), body, read=False)


def _delete_all(index: str, namespace: str, dry_run: bool) -> _Call:
    body: dict[str, Any] = {"namespace": namespace, "all": True, "confirm": namespace}
    if dry_run:
        body["dry_run"] = True
    return _post(_index(index, "/records/delete"), body, read=False)


def _count(index: str, namespace: str, selector: dict[str, Any]) -> _Call:
    return _post(_index(index, "/records/count"), {"namespace": namespace, **selector}, read=True)


# ── Jobs ──────────────────────────────────────────────────────────────


def _get_job(index: str, job_id: str) -> _Call:
    return "GET", _index(index, f"/jobs/{segment(job_id)}"), None, True


def _cancel_job(index: str, job_id: str) -> _Call:
    return "POST", _index(index, f"/jobs/{segment(job_id)}/cancel"), None, False


# ── Search ────────────────────────────────────────────────────────────


def _search(
    index: str,
    namespace: str,
    k: int,
    mode: Optional[str],
    vector: Any,
    record_id: Optional[str],
    quality: Optional[str],
    dense: Optional[Mapping[str, Any]],
    sparse: Optional[Mapping[str, Any]],
    lexical: Optional[Mapping[str, Any]],
    fusion: Optional[Mapping[str, Any]],
    selector: dict[str, Any],
    projection: dict[str, Any],
    include_components: Optional[bool],
    require_complete: Optional[bool],
    timeout_ms: Optional[int],
) -> _Call:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if vector is not None and record_id is not None:
        raise ValueError("pass at most one of vector and record_id")
    shorthand = vector is not None or record_id is not None or quality is not None
    if dense is not None and shorthand:
        raise ValueError("pass dense, or vector / record_id / quality, not both")
    if dense is None and shorthand:
        if vector is None and record_id is None:
            raise ValueError("quality needs vector or record_id")
        dense = _put({}, vector=_seq(vector), record_id=record_id, quality=quality)
    legs = _put(
        {},
        dense=dict(dense) if dense is not None else None,
        sparse=dict(sparse) if sparse is not None else None,
        lexical=dict(lexical) if lexical is not None else None,
    )
    if not legs:
        raise ValueError("search needs a query: vector, record_id, dense, sparse or lexical")
    if mode is None:
        if len(legs) > 1:
            mode = "hybrid"
        elif "lexical" in legs:
            mode = "lexical"
        else:
            mode = "semantic"
    body: dict[str, Any] = {"namespace": namespace, "mode": mode, "k": k, **legs}
    body = _put(body, fusion=dict(fusion) if fusion is not None else None)
    body.update(selector)
    body.update(projection)
    body = _put(
        body,
        include_components=include_components,
        require_complete=require_complete,
        timeout_ms=timeout_ms,
    )
    return _post(_index(index, "/search"), body, read=True)


# ── Upsert batching ───────────────────────────────────────────────────


class _UpsertBatcher:
    """Splits records into request bodies under both engine bounds.

    Every record lands in exactly one body, in input order, encoded once.
    Nothing is truncated: a record that cannot fit a request on its own is
    refused before its batch is sent."""

    def __init__(self, namespace: str, batch_size: int, max_bytes: int) -> None:
        if not isinstance(namespace, str):
            raise TypeError("namespace must be a string")
        if type(batch_size) is not int or not 1 <= batch_size <= UPSERT_MAX_RECORDS:
            raise ValueError(f"batch_size must be an integer from 1 to {UPSERT_MAX_RECORDS}")
        self._head = b'{"namespace":' + _encode(namespace) + b',"records":['
        self._overhead = len(self._head) + 2  # b"]}"
        if type(max_bytes) is not int or not self._overhead < max_bytes <= UPSERT_MAX_BODY_BYTES:
            raise ValueError(f"max_bytes must be an integer up to {UPSERT_MAX_BODY_BYTES}")
        self._batch_size = batch_size
        self._max_bytes = max_bytes
        self._items: List[bytes] = []
        self._size = self._overhead
        self.offset = 0

    @property
    def pending(self) -> int:
        return len(self._items)

    def add(self, record: Any) -> List[Tuple[int, int, bytes]]:
        """Encode ``record``; return the batches that are now ready to send."""
        if not isinstance(record, Mapping):
            raise TypeError("each record must be a mapping")
        raw = _encode(dict(record))
        if self._overhead + len(raw) > self._max_bytes:
            raise ValueError(
                f"record {record.get('id')!r} is {len(raw)} bytes encoded, over the "
                f"{self._max_bytes}-byte request bound on its own"
            )
        ready: List[Tuple[int, int, bytes]] = []
        if self._items and self._size + 1 + len(raw) > self._max_bytes:
            ready.append(self._take())
        self._size += len(raw) + (1 if self._items else 0)
        self._items.append(raw)
        if len(self._items) == self._batch_size:
            ready.append(self._take())
        return ready

    def finish(self) -> List[Tuple[int, int, bytes]]:
        return [self._take()] if self._items else []

    def _take(self) -> Tuple[int, int, bytes]:
        body = self._head + b",".join(self._items) + b"]}"
        batch = (self.offset, len(self._items), body)
        self.offset += len(self._items)
        self._items, self._size = [], self._overhead
        return batch


def _ack(answer: Any, offset: int, count: int) -> UpsertBatchResult:
    upserted = answer.get("upserted_count") if isinstance(answer, dict) else None
    if type(upserted) is not int or not 0 <= upserted <= count:
        raise OCProtocolError("upsert response must contain a valid upserted_count")
    return UpsertBatchResult(
        offset=offset,
        count=count,
        upserted_count=upserted,
        namespace_created=bool(answer.get("namespace_created", False)),
        receipt=answer.get("receipt"),
    )


def _new_total() -> dict[str, Any]:
    return {"upserted_count": 0, "namespace_created": False, "batches": 0, "receipts": []}


def _add_total(total: dict[str, Any], batch: UpsertBatchResult) -> None:
    total["upserted_count"] += batch.upserted_count
    total["namespace_created"] = total["namespace_created"] or batch.namespace_created
    total["batches"] += 1
    total["receipts"].append(batch.receipt)


def _check_cursor(previous: Optional[str], cursor: Any) -> Optional[str]:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or cursor == previous:
        raise OCProtocolError("page response carried an invalid next_cursor")
    return cursor


def _page_items(page: Any, key: str) -> List[Any]:
    items = page.get(key) if isinstance(page, dict) else None
    if key == "records" and isinstance(items, dict):
        return list(items.values())
    if not isinstance(items, list):
        raise OCProtocolError(f"page response must contain {key}")
    return items


# ── Sync namespace ────────────────────────────────────────────────────


class _VectorIndexesNamespace:
    """``client.vector_indexes`` on the sync client."""

    def __init__(self, parent: "OriginChain") -> None:
        self._p = parent

    def _send(self, call: _Call) -> Any:
        method, path, body, read = call
        if body is None:
            return self._p._request(method, path, retry_safe=read).json()
        return self._p._request(
            method, path, content=body, headers=dict(_JSON), retry_safe=read
        ).json()

    # Indexes

    def create_index(
        self,
        name: str,
        *,
        dimension: Optional[int] = None,
        metric: Optional[str] = None,
        sparse: Optional[bool] = None,
        text_fields: Optional[Sequence[Mapping[str, str]]] = None,
        visibility_fields: Optional[Sequence[str]] = None,
        identifier_profile: Optional[str] = None,
        deletion_protection: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        """Create an index and return it. At least one of ``dimension``,
        ``sparse=True`` or ``text_fields`` is required.

        ``metric`` is ``"cosine"``, ``"dot"``, ``"l2"`` or ``"manhattan"``
        and goes with ``dimension``. ``text_fields`` is a list of
        ``{"name": ..., "analyzer": ...}``. ``identifier_profile`` is
        ``"unicode"`` (the default) or ``"ascii"``. A name that is already
        taken raises with ``code == "index_already_exists"`` (409); creation
        is not sent twice, so after a lost answer, describe the index."""
        return self._send(  # type: ignore[no-any-return]
            _create_index(
                name, dimension, metric, sparse, text_fields, visibility_fields,
                identifier_profile, deletion_protection, tags,
            )
        )

    def describe_index(self, index: str) -> dict[str, Any]:
        """Return one index by name or ``index_id``."""
        return self._send(_describe_index(index))  # type: ignore[no-any-return]

    def list_indexes(
        self,
        *,
        name: Optional[str] = None,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """One page of live indexes in ascending name order:
        ``{"indexes": [...], "next_cursor": ...}``. ``next_cursor`` is absent
        on the last page. Send at most one of ``name`` and ``prefix``."""
        return self._send(_list_indexes(name, prefix, limit, cursor))  # type: ignore[no-any-return]

    def iter_indexes(
        self, *, prefix: Optional[str] = None, limit: Optional[int] = None
    ) -> Iterator[dict[str, Any]]:
        """Every live index, one page in memory at a time and no prefetch."""
        cursor: Optional[str] = None
        while True:
            page = self.list_indexes(prefix=prefix, limit=limit, cursor=cursor)
            yield from _page_items(page, "indexes")
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    def configure_index(
        self,
        index: str,
        *,
        deletion_protection: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        """Change ``deletion_protection`` (``"enabled"`` / ``"disabled"``)
        or ``tags`` (a tag set to ``""`` is removed). Returns the index."""
        return self._send(_configure_index(index, deletion_protection, tags))  # type: ignore[no-any-return]

    def delete_index(self, index: str) -> dict[str, Any]:
        """Start deleting an index and every record in it. Returns the index
        with ``state == "deleting"``. An index with deletion protection
        raises with ``code == "deletion_protection_enabled"``."""
        return self._send(_delete_index(index))  # type: ignore[no-any-return]

    def index_stats(self, index: str) -> dict[str, Any]:
        """Record counts per namespace and in total:
        ``{"namespaces": [...], "total_record_count", "truncated", ...}``.
        At most 1,000 namespaces are listed; page the rest with
        :meth:`list_namespaces`."""
        return self._send(_index_stats(index))  # type: ignore[no-any-return]

    # Namespaces

    def create_namespace(
        self, index: str, name: str, *, declared_capacity: Optional[int] = None
    ) -> dict[str, Any]:
        """Create a namespace. An upsert also creates its namespace; create
        one explicitly to set ``declared_capacity``, the most records it may
        hold."""
        return self._send(_create_namespace(index, name, declared_capacity))  # type: ignore[no-any-return]

    def describe_namespace(self, index: str, name: str) -> dict[str, Any]:
        """One namespace, with its ``record_count``."""
        return self._send(_describe_namespace(index, name))  # type: ignore[no-any-return]

    def list_namespaces(
        self,
        index: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """One page of an index's namespaces:
        ``{"namespaces": [...], "next_cursor": ...}``."""
        return self._send(_list_namespaces(index, prefix, limit, cursor))  # type: ignore[no-any-return]

    def iter_namespaces(
        self, index: str, *, prefix: Optional[str] = None, limit: Optional[int] = None
    ) -> Iterator[dict[str, Any]]:
        """Every namespace of an index, one page in memory at a time."""
        cursor: Optional[str] = None
        while True:
            page = self.list_namespaces(index, prefix=prefix, limit=limit, cursor=cursor)
            yield from _page_items(page, "namespaces")
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    def configure_namespace(
        self, index: str, name: str, *, declared_capacity: Optional[int]
    ) -> dict[str, Any]:
        """Set ``declared_capacity``, or remove it with ``None``. A value
        below the records already held is refused (409)."""
        return self._send(_configure_namespace(index, name, declared_capacity))  # type: ignore[no-any-return]

    def delete_namespace(self, index: str, name: str) -> dict[str, Any]:
        """Delete a namespace and every record in it. The engine requires
        the name to be confirmed; this method sends the confirmation, so
        calling it is the confirmation. The default namespace cannot be
        deleted (400, ``namespace_reserved``): clear it with
        :meth:`delete_all`."""
        return self._send(_delete_namespace(index, name))  # type: ignore[no-any-return]

    def build_index(
        self, index: str, namespace: str, *, seed: Optional[int] = None
    ) -> dict[str, Any]:
        """Build a namespace's disk IVF-PQ index as a background job:
        ``{"job_id", "receipt"}``. Poll :meth:`get_job`."""
        return self._send(_build_index(index, namespace, seed))  # type: ignore[no-any-return]

    # Records

    def upsert(
        self,
        index: str,
        namespace: str,
        records: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = UPSERT_MAX_RECORDS,
        max_bytes: int = UPSERT_MAX_BODY_BYTES,
    ) -> dict[str, Any]:
        """Write records, replacing any record with the same id, and return
        ``{"upserted_count", "namespace_created", "batches", "receipts"}``.

        Each record is ``{"id": ..., "values": [...], "sparse_values":
        {"indices": [...], "values": [...]}, "metadata": {...}, "text":
        {...}}``; only ``id`` is always required. An upsert replaces the
        whole record. The namespace is created on its first write.

        The input is sent in requests of at most ``batch_size`` records and
        ``max_bytes`` bytes (the engine's limits are 1,000 and 2,000,000),
        in order, and each record exactly once. Each request commits on its
        own. A failure raises :class:`~originchain.BatchWriteError` naming
        the input range that was not acknowledged (``offset``, ``count``);
        every record before ``offset`` is written. Its ``__cause__`` is the
        underlying error. Nothing is sent again automatically."""
        total = _new_total()
        for batch in self.upsert_batches(
            index, namespace, records, batch_size=batch_size, max_bytes=max_bytes
        ):
            _add_total(total, batch)
        return total

    def upsert_batches(
        self,
        index: str,
        namespace: str,
        records: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = UPSERT_MAX_RECORDS,
        max_bytes: int = UPSERT_MAX_BODY_BYTES,
    ) -> Iterator[UpsertBatchResult]:
        """:meth:`upsert`, lazily: yields one :class:`UpsertBatchResult` per
        acknowledged request. Reads the input as it goes, so only one batch
        is held in memory."""
        path = _index(index, "/records/upsert")
        batcher = _UpsertBatcher(namespace, batch_size, max_bytes)
        return self._upsert_iter(path, batcher, records)

    def _upsert_iter(
        self, path: str, batcher: _UpsertBatcher, records: Iterable[Mapping[str, Any]]
    ) -> Iterator[UpsertBatchResult]:
        source = iter(records)

        def send(batch: Tuple[int, int, bytes]) -> UpsertBatchResult:
            offset, count, body = batch
            try:
                return _ack(self._send(("POST", path, body, False)), offset, count)
            except Exception as error:
                raise BatchWriteError(offset, count, "unknown") from error

        try:
            while True:
                try:
                    record = next(source)
                except StopIteration:
                    break
                except Exception as error:
                    raise BatchWriteError(batcher.offset, batcher.pending, "not_sent") from error
                try:
                    ready = batcher.add(record)
                except Exception as error:
                    raise BatchWriteError(
                        batcher.offset, batcher.pending + 1, "not_sent"
                    ) from error
                for batch in ready:
                    yield send(batch)
            for batch in batcher.finish():
                yield send(batch)
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()

    def fetch(
        self,
        index: str,
        namespace: str,
        ids: Sequence[str],
        *,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Read records by id (1 to 1,000 unique ids). Returns ``{"records":
        {id: record}, "missing": [ids], "namespace", "receipt_position"}``.
        Metadata is returned unless ``include_metadata=False``; values and
        sparse values only when asked for."""
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return self._send(_fetch(index, namespace, ids, projection))  # type: ignore[no-any-return]

    def fetch_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """One page of the records matching a filter, in ascending id order:
        ``{"records": {id: record}, "namespace", "next_cursor"}``. Pass
        ``filter_expr`` (operators such as ``$eq``, ``$in``, ``$gt``,
        ``$and``) or ``filter`` (an equality map), not both. A page can be
        short, or empty, and still carry ``next_cursor``; only a page
        without one ends the listing."""
        selector = _one_filter(filter_expr, filter, required=True)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return self._send(  # type: ignore[no-any-return]
            _fetch_by_filter(index, namespace, selector, limit, cursor, projection)
        )

    def iter_records(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        limit: Optional[int] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> Iterator[dict[str, Any]]:
        """Every record matching a filter, from :meth:`fetch_by_filter`
        pages, one page in memory at a time. Not a snapshot: each page reads
        the records as they are when it is fetched."""
        selector = _one_filter(filter_expr, filter, required=True)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        cursor: Optional[str] = None
        while True:
            page = self._send(
                _fetch_by_filter(index, namespace, selector, limit, cursor, projection)
            )
            yield from _page_items(page, "records")
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    def list_ids(
        self,
        index: str,
        namespace: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """One page of record ids in ascending byte order:
        ``{"ids": [...], "next_cursor": ...}``."""
        return self._send(_list_ids(index, namespace, prefix, limit, cursor))  # type: ignore[no-any-return]

    def iter_ids(
        self,
        index: str,
        namespace: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[str]:
        """Every record id of a namespace, one page in memory at a time."""
        cursor: Optional[str] = None
        while True:
            page = self.list_ids(index, namespace, prefix=prefix, limit=limit, cursor=cursor)
            yield from _page_items(page, "ids")
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    def update(
        self,
        index: str,
        namespace: str,
        id: str,
        *,
        values: Optional[Sequence[float]] = None,
        sparse_values: Optional[Mapping[str, Any]] = None,
        metadata_merge: Optional[Mapping[str, Any]] = None,
        metadata_replace: Optional[Mapping[str, Any]] = None,
        metadata_remove: Optional[Sequence[str]] = None,
        text_set: Optional[Mapping[str, str]] = None,
        text_remove: Optional[Sequence[str]] = None,
        if_revision_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Change one record in place and return ``{"receipt"}``. Unlike
        :meth:`upsert`, fields that are not named are kept.
        ``metadata_merge`` sets keys, ``metadata_replace`` replaces the
        whole map, ``metadata_remove`` drops keys. With
        ``if_revision_token`` (from a fetch or a receipt) the update applies
        only if the record has not changed since. An absent record raises
        with ``code == "record_not_found"``."""
        return self._send(  # type: ignore[no-any-return]
            _update(
                index, namespace, id, values, sparse_values, metadata_merge, metadata_replace,
                metadata_remove, text_set, text_remove, if_revision_token,
            )
        )

    def update_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        metadata_merge: Optional[Mapping[str, Any]] = None,
        metadata_remove: Optional[Sequence[str]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Change the metadata of every record matching a filter, as a
        background job: ``{"job_id", "receipt"}``; poll :meth:`get_job`. A
        job updates at most 100,000 records. With ``dry_run=True`` nothing
        is written: ``{"dry_run": True, "matched_records", "sample_ids"}``."""
        selector = _one_filter(filter_expr, filter, required=True)
        return self._send(  # type: ignore[no-any-return]
            _update_by_filter(index, namespace, selector, metadata_merge, metadata_remove, dry_run)
        )

    def delete(self, index: str, namespace: str, ids: Sequence[str]) -> dict[str, Any]:
        """Delete records by id (1 to 1,000 unique ids). An id with no record
        counts as missing. Returns ``{"deleted", "missing", "receipt"}``."""
        return self._send(_delete_ids(index, namespace, ids))  # type: ignore[no-any-return]

    def delete_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete every record of a namespace that matches a filter, as a
        background job: ``{"job_id", "receipt"}``; poll :meth:`get_job`.
        With ``dry_run=True`` nothing is written and the answer is
        ``{"dry_run": True, "matched_records", "sample_ids"}``."""
        selector = _one_filter(filter_expr, filter, required=True)
        return self._send(_delete_by_filter(index, namespace, selector, dry_run))  # type: ignore[no-any-return]

    def delete_all(self, index: str, namespace: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Delete every record of a namespace and keep the namespace. The
        engine requires the namespace to be confirmed; this method sends the
        confirmation, so calling it is the confirmation. With
        ``dry_run=True`` nothing is written: ``{"dry_run": True,
        "record_count"}``."""
        return self._send(_delete_all(index, namespace, dry_run))  # type: ignore[no-any-return]

    def count(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """The exact number of records, optionally matching a filter:
        ``{"count", "namespace"}``."""
        selector = _one_filter(filter_expr, filter, required=False)
        return self._send(_count(index, namespace, selector))  # type: ignore[no-any-return]

    # Jobs

    def get_job(self, index: str, job_id: str) -> dict[str, Any]:
        """A background job: ``{"job_id", "kind", "state", "progress", ...}``.
        It is finished once ``state`` is no longer ``queued`` or
        ``running``."""
        return self._send(_get_job(index, job_id))  # type: ignore[no-any-return]

    def cancel_job(self, index: str, job_id: str) -> dict[str, Any]:
        """Stop a job's further rounds. Not a rollback: records it already
        changed stay changed."""
        return self._send(_cancel_job(index, job_id))  # type: ignore[no-any-return]

    # Search

    def search(
        self,
        index: str,
        namespace: str,
        *,
        k: int,
        vector: Optional[Sequence[float]] = None,
        record_id: Optional[str] = None,
        quality: Optional[str] = None,
        dense: Optional[Mapping[str, Any]] = None,
        sparse: Optional[Mapping[str, Any]] = None,
        lexical: Optional[Mapping[str, Any]] = None,
        fusion: Optional[Mapping[str, Any]] = None,
        mode: Optional[str] = None,
        filter_expr: Any = None,
        filter: Optional[Mapping[str, Any]] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
        include_components: Optional[bool] = None,
        require_complete: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        """The ``k`` best records, best first.

        Query legs:

        - dense: ``vector=[...]`` or ``record_id=...`` (the stored vector of
          a record), with ``quality`` ``"fast"``, ``"high_recall"`` (the
          default) or ``"exact"``; or the whole leg as ``dense={...}``
          (adds ``candidates``).
        - ``sparse={"indices": [...], "values": [...]}`` (optional
          ``scorer`` ``"cosine"`` or ``"dot"``, ``candidates``).
        - ``lexical={"field": ..., "query": ...}`` (BM25 over a declared
          text field; optional ``match`` ``"terms"`` or ``"phrase"``).

        One leg is a ``"semantic"`` (dense or sparse) or ``"lexical"``
        search; two or more are ``"hybrid"``, fused by reciprocal rank
        (``fusion={"method": "rrf", "rrf_k": 60}`` is the default).
        ``mode`` overrides that choice.

        ``filter_expr`` (operators) or ``filter`` (equality map) restricts
        the records ranked; nothing is filtered after the top ``k`` is cut.
        Search returns metadata only with ``include_metadata=True``.

        Returns ``{"hits": [{"id", "score", "rank", ...}], "legs",
        "completeness", "returned", ...}``. Scores are always
        higher-is-better; ``completeness["value"]`` says whether an
        approximate leg may have missed records."""
        selector = _one_filter(filter_expr, filter, required=False)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return self._send(  # type: ignore[no-any-return]
            _search(
                index, namespace, k, mode, vector, record_id, quality, dense, sparse, lexical,
                fusion, selector, projection, include_components, require_complete, timeout_ms,
            )
        )


# ── Async namespace ───────────────────────────────────────────────────


class _AsyncVectorIndexesNamespace:
    """``client.vector_indexes`` on the async client. Same methods, same
    requests and same answers as the sync client's."""

    def __init__(self, parent: "AsyncOriginChain") -> None:
        self._p = parent

    async def _send(self, call: _Call) -> Any:
        method, path, body, read = call
        if body is None:
            return (await self._p._request(method, path, retry_safe=read)).json()
        resp = await self._p._request(
            method, path, content=body, headers=dict(_JSON), retry_safe=read
        )
        return resp.json()

    async def create_index(
        self,
        name: str,
        *,
        dimension: Optional[int] = None,
        metric: Optional[str] = None,
        sparse: Optional[bool] = None,
        text_fields: Optional[Sequence[Mapping[str, str]]] = None,
        visibility_fields: Optional[Sequence[str]] = None,
        identifier_profile: Optional[str] = None,
        deletion_protection: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        return await self._send(  # type: ignore[no-any-return]
            _create_index(
                name, dimension, metric, sparse, text_fields, visibility_fields,
                identifier_profile, deletion_protection, tags,
            )
        )

    async def describe_index(self, index: str) -> dict[str, Any]:
        return await self._send(_describe_index(index))  # type: ignore[no-any-return]

    async def list_indexes(
        self,
        *,
        name: Optional[str] = None,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._send(_list_indexes(name, prefix, limit, cursor))  # type: ignore[no-any-return]

    async def iter_indexes(
        self, *, prefix: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            page = await self.list_indexes(prefix=prefix, limit=limit, cursor=cursor)
            for item in _page_items(page, "indexes"):
                yield item
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    async def configure_index(
        self,
        index: str,
        *,
        deletion_protection: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        return await self._send(_configure_index(index, deletion_protection, tags))  # type: ignore[no-any-return]

    async def delete_index(self, index: str) -> dict[str, Any]:
        return await self._send(_delete_index(index))  # type: ignore[no-any-return]

    async def index_stats(self, index: str) -> dict[str, Any]:
        return await self._send(_index_stats(index))  # type: ignore[no-any-return]

    async def create_namespace(
        self, index: str, name: str, *, declared_capacity: Optional[int] = None
    ) -> dict[str, Any]:
        return await self._send(_create_namespace(index, name, declared_capacity))  # type: ignore[no-any-return]

    async def describe_namespace(self, index: str, name: str) -> dict[str, Any]:
        return await self._send(_describe_namespace(index, name))  # type: ignore[no-any-return]

    async def list_namespaces(
        self,
        index: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._send(_list_namespaces(index, prefix, limit, cursor))  # type: ignore[no-any-return]

    async def iter_namespaces(
        self, index: str, *, prefix: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            page = await self.list_namespaces(index, prefix=prefix, limit=limit, cursor=cursor)
            for item in _page_items(page, "namespaces"):
                yield item
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    async def configure_namespace(
        self, index: str, name: str, *, declared_capacity: Optional[int]
    ) -> dict[str, Any]:
        return await self._send(_configure_namespace(index, name, declared_capacity))  # type: ignore[no-any-return]

    async def delete_namespace(self, index: str, name: str) -> dict[str, Any]:
        return await self._send(_delete_namespace(index, name))  # type: ignore[no-any-return]

    async def build_index(
        self, index: str, namespace: str, *, seed: Optional[int] = None
    ) -> dict[str, Any]:
        return await self._send(_build_index(index, namespace, seed))  # type: ignore[no-any-return]

    async def upsert(
        self,
        index: str,
        namespace: str,
        records: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
        *,
        batch_size: int = UPSERT_MAX_RECORDS,
        max_bytes: int = UPSERT_MAX_BODY_BYTES,
    ) -> dict[str, Any]:
        total = _new_total()
        async for batch in self.upsert_batches(
            index, namespace, records, batch_size=batch_size, max_bytes=max_bytes
        ):
            _add_total(total, batch)
        return total

    def upsert_batches(
        self,
        index: str,
        namespace: str,
        records: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
        *,
        batch_size: int = UPSERT_MAX_RECORDS,
        max_bytes: int = UPSERT_MAX_BODY_BYTES,
    ) -> AsyncIterator[UpsertBatchResult]:
        path = _index(index, "/records/upsert")
        batcher = _UpsertBatcher(namespace, batch_size, max_bytes)
        return self._upsert_iter(path, batcher, records)

    async def _upsert_iter(
        self,
        path: str,
        batcher: _UpsertBatcher,
        records: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
    ) -> AsyncIterator[UpsertBatchResult]:
        is_async = isinstance(records, AsyncIterable)
        source: Any = records.__aiter__() if isinstance(records, AsyncIterable) else iter(records)

        async def send(batch: Tuple[int, int, bytes]) -> UpsertBatchResult:
            offset, count, body = batch
            try:
                return _ack(await self._send(("POST", path, body, False)), offset, count)
            except Exception as error:
                raise BatchWriteError(offset, count, "unknown") from error

        try:
            while True:
                try:
                    record = await source.__anext__() if is_async else next(source)
                except (StopIteration, StopAsyncIteration):
                    break
                except Exception as error:
                    raise BatchWriteError(batcher.offset, batcher.pending, "not_sent") from error
                try:
                    ready = batcher.add(record)
                except Exception as error:
                    raise BatchWriteError(
                        batcher.offset, batcher.pending + 1, "not_sent"
                    ) from error
                for batch in ready:
                    yield await send(batch)
            for batch in batcher.finish():
                yield await send(batch)
        finally:
            close = getattr(source, "aclose" if is_async else "close", None)
            if close is not None:
                result = close()
                if is_async:
                    await result

    async def fetch(
        self,
        index: str,
        namespace: str,
        ids: Sequence[str],
        *,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return await self._send(_fetch(index, namespace, ids, projection))  # type: ignore[no-any-return]

    async def fetch_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        selector = _one_filter(filter_expr, filter, required=True)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return await self._send(  # type: ignore[no-any-return]
            _fetch_by_filter(index, namespace, selector, limit, cursor, projection)
        )

    async def iter_records(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        limit: Optional[int] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        selector = _one_filter(filter_expr, filter, required=True)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        cursor: Optional[str] = None
        while True:
            page = await self._send(
                _fetch_by_filter(index, namespace, selector, limit, cursor, projection)
            )
            for item in _page_items(page, "records"):
                yield item
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    async def list_ids(
        self,
        index: str,
        namespace: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._send(_list_ids(index, namespace, prefix, limit, cursor))  # type: ignore[no-any-return]

    async def iter_ids(
        self,
        index: str,
        namespace: str,
        *,
        prefix: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[str]:
        cursor: Optional[str] = None
        while True:
            page = await self.list_ids(index, namespace, prefix=prefix, limit=limit, cursor=cursor)
            for item in _page_items(page, "ids"):
                yield item
            cursor = _check_cursor(cursor, page.get("next_cursor"))
            if cursor is None:
                return

    async def update(
        self,
        index: str,
        namespace: str,
        id: str,
        *,
        values: Optional[Sequence[float]] = None,
        sparse_values: Optional[Mapping[str, Any]] = None,
        metadata_merge: Optional[Mapping[str, Any]] = None,
        metadata_replace: Optional[Mapping[str, Any]] = None,
        metadata_remove: Optional[Sequence[str]] = None,
        text_set: Optional[Mapping[str, str]] = None,
        text_remove: Optional[Sequence[str]] = None,
        if_revision_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._send(  # type: ignore[no-any-return]
            _update(
                index, namespace, id, values, sparse_values, metadata_merge, metadata_replace,
                metadata_remove, text_set, text_remove, if_revision_token,
            )
        )

    async def update_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        metadata_merge: Optional[Mapping[str, Any]] = None,
        metadata_remove: Optional[Sequence[str]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        selector = _one_filter(filter_expr, filter, required=True)
        return await self._send(  # type: ignore[no-any-return]
            _update_by_filter(index, namespace, selector, metadata_merge, metadata_remove, dry_run)
        )

    async def delete(self, index: str, namespace: str, ids: Sequence[str]) -> dict[str, Any]:
        return await self._send(_delete_ids(index, namespace, ids))  # type: ignore[no-any-return]

    async def delete_by_filter(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        selector = _one_filter(filter_expr, filter, required=True)
        return await self._send(_delete_by_filter(index, namespace, selector, dry_run))  # type: ignore[no-any-return]

    async def delete_all(
        self, index: str, namespace: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        return await self._send(_delete_all(index, namespace, dry_run))  # type: ignore[no-any-return]

    async def count(
        self,
        index: str,
        namespace: str,
        filter_expr: Any = None,
        *,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        selector = _one_filter(filter_expr, filter, required=False)
        return await self._send(_count(index, namespace, selector))  # type: ignore[no-any-return]

    async def get_job(self, index: str, job_id: str) -> dict[str, Any]:
        return await self._send(_get_job(index, job_id))  # type: ignore[no-any-return]

    async def cancel_job(self, index: str, job_id: str) -> dict[str, Any]:
        return await self._send(_cancel_job(index, job_id))  # type: ignore[no-any-return]

    async def search(
        self,
        index: str,
        namespace: str,
        *,
        k: int,
        vector: Optional[Sequence[float]] = None,
        record_id: Optional[str] = None,
        quality: Optional[str] = None,
        dense: Optional[Mapping[str, Any]] = None,
        sparse: Optional[Mapping[str, Any]] = None,
        lexical: Optional[Mapping[str, Any]] = None,
        fusion: Optional[Mapping[str, Any]] = None,
        mode: Optional[str] = None,
        filter_expr: Any = None,
        filter: Optional[Mapping[str, Any]] = None,
        include_values: Optional[bool] = None,
        include_sparse: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        metadata_fields: Optional[Sequence[str]] = None,
        text_fields: Optional[Sequence[str]] = None,
        include_components: Optional[bool] = None,
        require_complete: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        selector = _one_filter(filter_expr, filter, required=False)
        projection = _projection(
            include_values, include_sparse, include_metadata, metadata_fields, text_fields
        )
        return await self._send(  # type: ignore[no-any-return]
            _search(
                index, namespace, k, mode, vector, record_id, quality, dense, sparse, lexical,
                fusion, selector, projection, include_components, require_complete, timeout_ms,
            )
        )


__all__ = [
    "DEFAULT_NAMESPACE",
    "UPSERT_MAX_BODY_BYTES",
    "UPSERT_MAX_RECORDS",
    "UpsertBatchResult",
    "_AsyncVectorIndexesNamespace",
    "_VectorIndexesNamespace",
]
