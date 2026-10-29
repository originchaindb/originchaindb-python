"""Async client for OriginChain operations and typed namespaces.

Both clients are kept side-by-side rather than sharing a common base
because httpx's sync/async clients have subtly different timeout +
context-manager semantics. Duplication is the lesser evil; pyright
catches type errors. Both clients expose the same operation namespaces.
"""

# Same rationale as the sync client: httpx.Response.json() returns
# `Any`, which `mypy --strict` flags at every `return await
# resp.json()` even when the method's declared return type matches
# what the engine actually emits. Disable the single check at file
# scope.
# mypy: disable-error-code="no-any-return"

from __future__ import annotations

import asyncio
import json as _json
import os
import time as _time
import warnings
from typing import Any, AsyncIterable, AsyncIterator, Iterable, List, Literal, Mapping, Optional, Sequence

import httpx

from . import _diagnostics as _diag
from . import _retry
from ._identity import identity_headers
from ._query_metrics import QueryMetricsResponse, _query_metrics_params
from ._async_namespaces import (
    _AsyncAdminNamespace,
    _AsyncFtsNamespace,
    _AsyncGraphNamespaceExtended,
    _AsyncSqlNamespace,
    _AsyncVectorNamespace,
)
from .client import (
    DEFAULT_KEEPALIVE_EXPIRY_S,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    RETRYABLE_STATUSES,
    _HTTP2_AVAILABLE,
    _MUTATING_METHODS,
    _json_or_none,
    _new_idempotency_key,
    _pool_limits,
)
from .errors import (
    OCAuthError,
    OCError,
    OCNotFoundError,
    OCPaymentRequiredError,
    OCRateLimitedError,
    OCReplicationDegraded,
    OCServerError,
    OCValidationError,
)
from .models import (
    DijkstraResult,
    FtsHit,
    GraphBfsHit,
    GraphPath,
    Neighbor,
    SqlResponse,
    SqlSelect,
    TenantUsage,
    VectorHit,
    _decode_sql_response,
)


class _AsyncSchemas:
    def __init__(self, parent: "AsyncOriginChain") -> None:
        self._p = parent

    async def list(self) -> list[str]:
        r = await self._p._request("GET", f"/v1/tenants/{self._p.tenant}/schemas")
        return r.json()

    async def get(self, schema: str) -> str:
        r = await self._p._request("GET", f"/v1/tenants/{self._p.tenant}/schemas/{schema}")
        return r.text

    async def register(self, toml_source: str) -> dict[str, Any]:
        r = await self._p._request(
            "POST",
            f"/v1/tenants/{self._p.tenant}/schemas",
            content=toml_source.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        return r.json()


class _AsyncRows:
    def __init__(self, parent: "AsyncOriginChain") -> None:
        self._p = parent

    async def get(self, schema: str, pk: str) -> dict[str, Any]:
        """Return row JSON unchanged, including temporal text or legacy milliseconds.

        This endpoint has no column-type envelope: strings such as ``NaN``
        are preserved rather than guessed to be floating-point values.
        """
        r = await self._p._request("GET", f"/v1/tenants/{self._p.tenant}/rows/{schema}/{pk}")
        return r.json()

    async def put(
        self,
        schema: str,
        row: Mapping[str, Any],
        *,
        expect_insert: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {"expect": "insert"} if expect_insert else None
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        r = await self._p._request(
            "POST",
            f"/v1/tenants/{self._p.tenant}/rows/{schema}",
            json=row,
            params=params,
            headers=headers,
        )
        return r.json()


    async def put_batch(
        self,
        schema: str,
        rows: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
        *,
        expect_insert: bool = False,
        idempotency_key: Optional[str] = None,
        chunk: int = 1000,
    ) -> int:
        """Upload rows sequentially in bounded chunks; return accepted rows.

        Accepts ordinary or asynchronous iterables. Each submitted chunk has a
        stable ``<idempotency_key>-c<index>`` key, independent of its accepted
        row count. Writes are not automatically retried. On failure or task
        cancellation, earlier chunks may already be durable; subsequent chunks
        are not consumed or sent. To replay after checking the server outcome,
        reuse the same base key, row ordering and chunk size.
        """
        if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk <= 0:
            raise ValueError("chunk must be a positive integer")
        params = {"expect": "insert"} if expect_insert else None
        base_idem = idempotency_key or _new_idempotency_key()
        total = 0
        chunk_no = 0
        chunk_buf: list[Mapping[str, Any]] = []
        async for row in _iterate_rows(rows):
            chunk_buf.append(row)
            if len(chunk_buf) >= chunk:
                total += await self._send_batch(schema, chunk_buf, params, base_idem, chunk_no)
                chunk_no += 1
                chunk_buf = []
                # Some transports complete immediately. Still give cancellation
                # and other tasks a chance before consuming the next chunk.
                await asyncio.sleep(0)
        if chunk_buf:
            total += await self._send_batch(schema, chunk_buf, params, base_idem, chunk_no)
        return total

    async def _send_batch(
        self,
        schema: str,
        rows: list[Mapping[str, Any]],
        params: Optional[dict[str, Any]],
        base_idem: str,
        chunk_no: int,
    ) -> int:
        response = await self._p._request(
            "POST",
            f"/v1/tenants/{self._p.tenant}/rows/{schema}/_batch",
            json=list(rows),
            params=params,
            headers={"Idempotency-Key": f"{base_idem}-c{chunk_no}"},
        )
        return response.json().get("inserted", 0)


async def _iterate_rows(
    rows: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
) -> AsyncIterator[Mapping[str, Any]]:
    if isinstance(rows, AsyncIterable):
        async for row in rows:
            yield row
    else:
        for row in rows:
            yield row


class _AsyncGraph(_AsyncGraphNamespaceExtended):
    """Async ``db.graph.*`` namespace. Mirrors the sync ``_Graph``."""

    def __init__(self, parent: "AsyncOriginChain") -> None:
        self._p = parent

    async def neighbors(self, schema: str, *, rel: str, pk: str) -> List[Neighbor]:
        params = {"rel": rel, "pk": pk}
        r = await self._p._request(
            "GET",
            f"/v1/tenants/{self._p.tenant}/graph/{schema}/neighbors",
            params=params,
        )
        return [Neighbor(pk=str(p), depth=1) for p in r.json()]

    async def reverse_neighbors(
        self, schema: str, *, rel: str, pk: str
    ) -> List[Neighbor]:
        params = {"rel": rel, "pk": pk}
        r = await self._p._request(
            "GET",
            f"/v1/tenants/{self._p.tenant}/graph/{schema}/reverse",
            params=params,
        )
        return [Neighbor(pk=str(p), depth=1) for p in r.json()]

    async def bfs(
        self,
        schema: str,
        *,
        rel: str,
        pk: str,
        max_depth: int = 3,
    ) -> List[GraphBfsHit]:
        params = {"rel": rel, "pk": pk, "max_depth": str(max_depth)}
        r = await self._p._request(
            "GET",
            f"/v1/tenants/{self._p.tenant}/graph/{schema}/bfs",
            params=params,
        )
        return [GraphBfsHit._from_payload(h) for h in r.json()]

    async def path(
        self,
        schema: str,
        *,
        rel: str,
        src: str,
        dst: str,
        max_depth: int = 3,
    ) -> GraphPath:
        params = {"rel": rel, "src": src, "dst": dst, "max_depth": str(max_depth)}
        r = await self._p._request(
            "GET",
            f"/v1/tenants/{self._p.tenant}/graph/{schema}/path",
            params=params,
        )
        body = r.json()
        return GraphPath(reachable=bool(body.get("reachable", False)))

    async def dijkstra(
        self,
        schema: str,
        *,
        rel: str,
        src: str,
        dst: str,
        weights: Mapping[str, float],
    ) -> DijkstraResult:
        params = {
            "rel": rel,
            "src": src,
            "dst": dst,
            "weights_json": _json.dumps(dict(weights)),
        }
        r = await self._p._request(
            "GET",
            f"/v1/tenants/{self._p.tenant}/graph/{schema}/dijkstra",
            params=params,
        )
        body = r.json()
        cost = body.get("cost")
        return DijkstraResult(cost=None if cost is None else float(cost))


class AsyncOriginChain:
    """asyncio-native client. ``async with`` for resource cleanup."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer: str,
        tenant: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_time: float = _retry.DEFAULT_MAX_RETRY_TIME,
        verify: bool | str = True,
        user_agent: Optional[str] = None,
        keepalive_expiry: float = DEFAULT_KEEPALIVE_EXPIRY_S,
        diagnostics: bool = False,
        db_user: Optional[str] = None,
        db_key: Optional[str] = None,
    ) -> None:
        _retry.validate_options(max_retries, max_retry_time)
        identity = identity_headers(db_user, db_key)
        self.base_url = base_url.rstrip("/")
        self.bearer = bearer
        self.tenant = tenant
        self.max_retries = max_retries
        self.max_retry_time = max_retry_time
        # Opt-in client diagnostics (off by default); see `_diagnostics`.
        self._diagnostics: _diag.AsyncReporter | None = (
            _diag.AsyncReporter(self._send_diagnostics) if diagnostics else None
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            verify=verify,
            http2=_HTTP2_AVAILABLE,
            limits=_pool_limits(keepalive_expiry),
            headers={
                **identity,
                "Authorization": f"Bearer {bearer}",
                "User-Agent": user_agent or "originchaindb-python/0.11.0rc1",
            },
        )
        self.schemas = _AsyncSchemas(self)
        self.rows = _AsyncRows(self)
        self.graph = _AsyncGraph(self)
        self.sql = _AsyncSqlNamespace(self)
        self.vector = _AsyncVectorNamespace(self)
        self.fts = _AsyncFtsNamespace(self)
        self.admin = _AsyncAdminNamespace(self)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "AsyncOriginChain":
        try:
            base_url = os.environ["OC_BASE_URL"]
            bearer = os.environ["OC_BEARER"]
            tenant = os.environ["OC_TENANT"]
        except KeyError as e:
            raise OCError(f"missing required env var: {e.args[0]}") from None
        return cls(base_url=base_url, bearer=bearer, tenant=tenant, **kwargs)

    async def health(self) -> dict[str, Any]:
        r = await self._request("GET", "/health")
        return r.json()

    async def query(self, plan: dict[str, Any]) -> list[Any]:
        r = await self._request(
            "POST", f"/v1/tenants/{self.tenant}/query", json=plan
        )
        return r.json()

    async def query_metrics(
        self, minutes: int = 60, bucket_seconds: int = 60,
    ) -> QueryMetricsResponse:
        """Read query completion counts and mean latency for this process.

        Supported minute/bucket-second pairs are 60/60, 1440/600 and
        10080/3600. Results retain the JSON field names; timestamps are Unix
        milliseconds and idle buckets have ``mean_latency_ms=None``. Use
        ``observed_seconds`` for throughput, as coverage may be partial.
        Restart clears history; this is not an aggregate across replicas.

        Requires a tenant service credential or query administrator.
        Impersonated database-user headers are refused by the server (403).
        Uses this client's timeout, retries, identity and error handling.
        """
        response = await self._request(
            "GET", f"/v1/tenants/{self.tenant}/query-metrics",
            params=_query_metrics_params(minutes, bucket_seconds),
        )
        return response.json()

    async def usage(self) -> TenantUsage:
        """Live usage counters + the tenant's compute configuration.

        The response ``tier`` is the neutral configuration slug
        (``entry`` / ``standard`` / ``advanced`` / ``custom``); the
        internal weather codename is never exposed. Prefer
        :attr:`TenantUsage.configuration` for the full spec. Both are
        ``None`` in legacy per-addon mode."""
        r = await self._request("GET", f"/v1/tenants/{self.tenant}/usage")
        return TenantUsage._from_payload(r.json())

    # ── SQL ───────────────────────────────────────────────────────────

    async def _sql_callable_impl(
        self, query: str, params: Optional[Sequence[Any]] = None, *,
        retry_safe: bool = False, idempotency_key: Optional[str] = None,
    ) -> SqlResponse:
        """Implementation backing the legacy ``await client.sql(...)`` call."""
        from ._namespaces import _positional_params

        payload: dict[str, Any] = {"sql": query}
        if params is not None:
            payload["params"] = _positional_params(params)
        r = await self._request(
            "POST",
            f"/v1/tenants/{self.tenant}/sql",
            json=payload,
            retry_safe=retry_safe,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key is not None else None,
        )
        return _decode_sql_response(r.json())

    async def sql_one(
        self, query: str, params: Optional[Sequence[Any]] = None, *,
        retry_safe: bool = False, idempotency_key: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        resp = await self._sql_callable_impl(
            query, params, retry_safe=retry_safe, idempotency_key=idempotency_key
        )
        if not isinstance(resp, SqlSelect):
            raise OCValidationError(
                f"sql_one expected SELECT, got {type(resp).__name__}"
            )
        if not resp.rows:
            return None
        first = resp.rows[0]
        if not isinstance(first, dict):
            return {"value": first}
        return dict(first)

    # ── Vector ────────────────────────────────────────────────────────

    async def vector_put(
        self,
        table: str,
        *,
        id: str,
        embedding: list[float],
        dim: int,
        metric: str = "cosine",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        body: dict[str, Any] = {
            "id": id,
            "embedding": list(embedding),
            "dim": dim,
            "metric": metric,
        }
        if metadata is not None:
            body["metadata"] = dict(metadata)
        await self._request(
            "POST",
            f"/v1/tenants/{self.tenant}/vector/{table}/put",
            json=body,
        )

    async def vector_topk(
        self,
        table: str,
        *,
        query: list[float],
        k: int = 10,
        dim: int,
        metric: str = "cosine",
        filter: Optional[Mapping[str, Any]] = None,
        mode: Optional[Literal["fast", "high_recall"]] = None,
    ) -> list[VectorHit]:
        body: dict[str, Any] = {
            "query": list(query),
            "k": k,
            "dim": dim,
            "metric": metric,
        }
        if mode is not None:
            body["mode"] = mode
        if filter is not None:
            body["filter"] = dict(filter)
        r = await self._request(
            "POST",
            f"/v1/tenants/{self.tenant}/vector/{table}/topk",
            json=body,
        )
        return [VectorHit._from_payload(h) for h in r.json()]

    # ── Full-text ─────────────────────────────────────────────────────

    async def fts_index(
        self,
        table: str,
        field: str,
        *,
        doc_id: str,
        text: str,
    ) -> None:
        await self._request(
            "POST",
            f"/v1/tenants/{self.tenant}/fts/{table}/{field}",
            json={"doc_id": doc_id, "text": text},
        )

    async def fts_search(
        self,
        table: str,
        field: str,
        *,
        q: str,
        mode: str = "boolean",
        k: int = 10,
    ) -> list[FtsHit]:
        params: dict[str, str] = {"q": q, "mode": mode}
        if mode == "bm25":
            params["k"] = str(k)
        r = await self._request(
            "GET",
            f"/v1/tenants/{self.tenant}/fts/{table}/{field}",
            params=params,
        )
        body = r.json()
        if mode == "bm25":
            return [FtsHit._from_ranked(h) for h in body]
        return [FtsHit._from_doc_id(str(d)) for d in body]

    async def aclose(self) -> None:
        if self._diagnostics is not None:
            await self._diagnostics.close()  # sends what is queued first
        await self._client.aclose()

    async def flush_diagnostics(self) -> None:
        """Send any queued diagnostics now. A no-op when diagnostics are off.
        Never raises."""
        if self._diagnostics is not None:
            await self._diagnostics.flush()

    async def _send_diagnostics(self, batch: list[dict[str, Any]]) -> None:
        # Straight to the engine, not through `_request` (see the sync client).
        await self._client.post(
            f"/v1/tenants/{self.tenant}/diagnostics",
            json={"events": batch},
            timeout=_diag.SEND_TIMEOUT_S,
        )

    async def __aenter__(self) -> "AsyncOriginChain":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
        content: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        retry_safe: bool | None = None,
    ) -> httpx.Response:
        # A key only deduplicates endpoints that implement it; it does not
        # authorize replay of arbitrary writes or SQL statements.
        retries = self.max_retries if _retry.eligible(method, retry_safe) else 0
        deadline = _time.perf_counter() + self.max_retry_time
        if method.upper() in _MUTATING_METHODS:
            if headers is None:
                headers = {"Idempotency-Key": _new_idempotency_key()}
            elif not any(k.lower() == "idempotency-key" for k in headers):
                headers = {**headers, "Idempotency-Key": _new_idempotency_key()}
        # One logical request id per call, the same on every retry; the attempt
        # number changes. The engine records both next to its own request id.
        logical_id, headers = _diag.correlate(headers)
        report = self._diagnostics
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            started = _time.perf_counter()
            try:
                resp = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    content=content,
                    headers=_diag.with_attempt(headers, attempt + 1),
                )
            except httpx.RequestError as e:
                last_exc = e
                if report is not None:
                    report.push(_diag.event(
                        method=method, path=path, started=started,
                        logical_request_id=logical_id, attempt=attempt + 1, failure=e,
                    ))
                network_wait = self._backoff(attempt)
                if attempt < retries and network_wait < deadline - _time.perf_counter():
                    await asyncio.sleep(network_wait)
                    if _time.perf_counter() < deadline:
                        continue
                err = OCError(f"transport error: {e}")
                err.logical_request_id = logical_id
                raise err from e

            request_id = _diag.engine_request_id(resp.headers.get("X-OC-Request-Id"))
            if resp.status_code < 400:
                if report is not None:
                    report.push(_diag.event(
                        method=method, path=path, started=started,
                        logical_request_id=logical_id, attempt=attempt + 1,
                        status=resp.status_code, request_id=request_id,
                    ))
                if resp.headers.get("X-OC-Replication", "").lower() == "degraded":
                    warnings.warn(
                        "leader returned 200 but follower(s) didn't ack within"
                        " --sync-timeout-ms; write is durable but RPO=0 not met",
                        OCReplicationDegraded,
                        stacklevel=3,
                    )
                return resp

            if report is not None:
                report.push(_diag.event(
                    method=method, path=path, started=started,
                    logical_request_id=logical_id, attempt=attempt + 1,
                    status=resp.status_code, request_id=request_id,
                    code=_diag.error_code(_json_or_none(resp)),
                ))
            if resp.status_code in RETRYABLE_STATUSES and attempt < retries:
                wait = self._retry_after(resp)
                if wait is None:
                    wait = self._backoff(attempt)
                if wait < deadline - _time.perf_counter():
                    await resp.aclose()
                    await asyncio.sleep(wait)
                    if _time.perf_counter() < deadline:
                        continue

            try:
                self._raise_for(resp)
            except OCError as err:
                err.request_id = request_id
                err.logical_request_id = logical_id
                raise
        raise OCError(f"request failed after retries: {last_exc}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return _retry.backoff(attempt)

    @staticmethod
    def _retry_after(resp: httpx.Response) -> Optional[float]:
        return _retry.retry_after(resp.headers.get("Retry-After"))

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        msg = body.get("error") if isinstance(body, dict) else str(body)
        status = resp.status_code
        if status in (401, 403):
            raise OCAuthError(msg or "unauthorized", status=status, body=body)
        if status == 402:
            addon_msg = (
                body.get("msg") if isinstance(body, dict) else None
            ) or msg or "payment required (add-on)"
            raise OCPaymentRequiredError(addon_msg, status=status, body=body)
        if status == 404:
            raise OCNotFoundError(msg or "not found", status=status, body=body)
        if status == 400:
            raise OCValidationError(msg or "validation failed", status=status, body=body)
        if status == 429:
            retry_after = AsyncOriginChain._retry_after(resp)
            raise OCRateLimitedError(
                msg or "rate limited",
                retry_after=1.0 if retry_after is None else retry_after,
                status=status,
                body=body,
            )
        if 500 <= status < 600:
            raise OCServerError(msg or f"server error {status}", status=status, body=body)
        raise OCError(msg or f"unexpected status {status}", status=status, body=body)


__all__ = ["AsyncOriginChain"]
