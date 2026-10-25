# Changelog

Changes to the OriginChainDB Python SDK. Earlier entries describe package versions published before this repository was created.

## [Unreleased]

- Establish `originchaindb/originchaindb-python` as the maintained source repository.

- Remove the synchronous and asynchronous `ask()` helpers. Use SQL, full-text search, vector search, or graph operations directly.
- Remove the natural-language availability prerequisite from live and release verification. The four supported query families retain their live acceptance checks.
- Mark development builds as `0.11.0.dev0`; previously published packages are unchanged.

## [0.10.0rc5] - 2026-10-25

- Expose optional SQL `column_types` metadata in synchronous and asynchronous query/tagged results.
- Preserve projected GROUP BY keys, microsecond timestamp text, exact numeric strings and interval text across all row/query paths.
- Convert floating special strings only when SQL metadata explicitly identifies a floating type; preserve untyped, numeric and temporal strings.
- Document legacy millisecond values and server-dependent temporal/special-value support. Real stored-microsecond and special-value acceptance is still pending on the tested engine.

## [0.10.0rc4] - 2026-10-24

- Decode successful bulk DELETE responses without a `pk` field in both clients.
  `SqlDelete.pk` is now optional; omitted or null keys remain `None`.
- Preserve single-key responses and server-reported counts, including zero,
  large values and unknown counts. Buffered rows remain distinct from committed rows.

## [0.10.0rc3] - 2026-10-24

- Add `query_metrics(minutes=60, bucket_seconds=60)` to both clients with
  public `QueryMetricsResponse` / `QueryMetricsBucket` typed dictionaries.
- Preserve raw field names, wide integer timestamps and counters, and null
  idle latency. Reuse existing transport, retry and error behavior.
- Verify all three supported windows against a live database through both
  clients, plus fresh-wheel and mock transport checks for wide values and
  service authorization errors.

## [0.10.0rc2] - 2026-10-24

- Add native async SQL, vector, FTS, admin and extended graph namespaces with
  the same wire contracts and response models as the synchronous client.
  Preserve the callable SQL API and legacy async convenience methods.
- Add async row batching for ordinary and async iterables. Upload sequential
  bounded chunks, keep distinct stable per-chunk keys, reject invalid sizes
  before consuming input, and stop on failure or cancellation. Completed
  chunks remain subject to the server's durability and idempotency contract.
- Preserve SQL INSERT/DELETE execution counts and transaction-buffer counts.
  `sql.execute().rows_affected` is now `None` when the server did not confirm a
  count, replacing the incorrect inferred value of one for translated writes.
- Run shared transport contracts for both clients, expand CI to Windows and
  macOS, and verify fresh wheel installations and the PEP 561 marker.
- Add native async live acceptance checks and an explicit Ask-unavailable
  profile that only accepts HTTP 402 with the exact `ask_unavailable` code.

## [0.10.0] - 2026-10-23

- Stop automatic retries of SQL and writes; safe reads retain retries. Add a finite retry scheduling window, full jitter, HTTP-date Retry-After, and explicit SQL retry safety.
- Add positional parameters and stable idempotency keys to sync/async callable SQL and sql_one, and ship the PEP 561 typing marker.

- Derive batch idempotency keys from the input chunk sequence, independently of the server's inserted-row count, so a final partial chunk cannot reuse an earlier key.
- Reject invalid batch sizes before consuming input rows or sending requests.

## [0.8.0] â€” 2026-10-07

### Added

- **Request ids on every error.** `OCError.request_id` is the engine's id for
  the request (`X-OC-Request-Id`), to quote in a support request.
  `OCError.logical_request_id` is the id the client sent.
- **Request correlation.** Every call sends `X-OC-Logical-Request-Id` (a UUID,
  the same on every retry of the call) and `X-OC-Attempt` (1, 2, ...), which the
  engine records next to its own request id.
- **Opt-in diagnostics** (`diagnostics=True`, default off, both clients): each
  attempt's method, path, outcome, duration, status and request ids are reported
  to your own engine, which keeps only the route template. See the README's
  "Diagnostics" section. `flush_diagnostics()` sends anything queued; `close()`
  and `aclose()` do too.

## [0.7.0] â€” 2026-09-25

Faster repeat calls from applications that call the API every few seconds.

### Changed

- **Idle connections are kept for 300 s** (was httpx's default of 5 s).
  An application calling every few seconds used to re-open TCP + TLS on
  most calls â€” two extra network round trips each time, ~45-50 ms per call
  from a client one region away. The server never closes an idle keep-alive
  connection; the shortest idle limit on the path is the network load
  balancer's 350 s, so the client stays below it.
- Pool sizes stay at httpx's defaults (100 connections, 20 kept alive),
  now passed explicitly.

### Added

- `keepalive_expiry=` on `OriginChain(...)` and `AsyncOriginChain(...)`
  (seconds; clamped to 0-340).
- Default `User-Agent` is `originchain-python/0.7.0`.

## [0.6.0] â€” 2026-07-29

Two wire-format bug fixes. Both surfaces were **100% broken in 0.5.0** â€”
neither ever produced a successful engine response â€” so nothing that
works today changes behaviour.

### Fixed

- **`sql.query(params=...)` now sends positional bind parameters.**
  0.5.0 sent `params` as a named JSON object (`{"a": 1}`). The `/sql`
  handler binds positionally: `params` is a JSON **array** and `$1` /
  `$2` index into it. Every parameterised query failed. The parameter
  is now `Sequence[Any]` and reaches the wire verbatim as an array â€”
  the same shape the TypeScript client (`params?: unknown[]`) and the
  Go client (variadic `params ...any`) already send.
- **`sql.install_materialized_view(refresh_mode=...)` now sends live
  wire values.** 0.5.0 sent `"manual"` / `"on_write"`. The engine's
  `RefreshMode` enum is `#[serde(rename_all = "snake_case")]` over
  `OnDemand | Incremental`, and unknown values are a hard 400 â€” so
  every install failed. Accepted values are now `"on_demand"`
  (default) and `"incremental"`.

### Changed

- `refresh_mode` default is `"on_demand"` (was `"manual"`); same
  intended behaviour, correct wire value. `"manual"` / `"on_write"`
  still work as deprecated aliases for `"on_demand"` /
  `"incremental"` and emit a `DeprecationWarning`; they are removed in
  1.0. Any other value raises `OCValidationError` **before** the
  request is sent rather than round-tripping to a 400.
- `sql.query(params=...)` no longer accepts a `Mapping`. A `dict`
  raises `OCValidationError` naming the positional rewrite. Flattening
  a dict into a guessed order would bind values to placeholders the
  caller never wrote down, and the accompanying SQL would still carry
  named placeholders the engine can't parse â€” a clear error beats a
  silently-wrong-row bug. A bare `str` / `bytes` is rejected for the
  same reason (it would otherwise bind one parameter per character).

### Materialized-view refresh-mode semantics (documented, unchanged)

- `"on_demand"` â€” full recompute on an explicit
  `refresh_materialized_view()` call. The only mode to build against
  today.
- `"incremental"` â€” apply-time maintenance as source rows are written.
  **Aggregate** materialized views are behind a preview flag that
  ships OFF, so an incremental *aggregate* view currently returns
  `422`. Don't depend on it without confirming the flag for your
  instance.

### Migration from 0.5.0

- `db.sql.query("... WHERE s = :s", params={"s": "AAPL"})` â†’
  `db.sql.query("... WHERE s = $1", params=["AAPL"])`
- `refresh_mode="manual"` â†’ `refresh_mode="on_demand"`;
  `refresh_mode="on_write"` â†’ `refresh_mode="incremental"`
- Default `User-Agent` is `originchain-python/0.6.0`

## [0.5.0] â€” 2026-07-15

Everything below is new relative to 0.4.0 **as published on PyPI**
(see the 0.4.0 note): the typed-namespace batch plus the follow-on
engine surfaces ship together in this release.

### Typed namespaces (sync client; async parity planned)

- **`client.sql` / `client.vector` / `client.fts` / `client.graph`
  namespaces** on the sync `OriginChain` client. Customers no longer
  hand-roll dicts and parse JSON manually for the four
  substrate-extension surfaces:
  - `client.sql.query(...)` / `client.sql.execute(...)`; the callable
    `client.sql(query)` and `client.sql_one(query)` return a
    `SqlSelect` / `SqlInsert` / `SqlDelete` discriminated union.
  - `client.vector.put(...)` / `client.vector.topk(...)` (plus the
    legacy `vector_put` / `vector_topk` methods) â€” return
    `list[VectorHit]`.
  - `client.fts.index(...)` / `client.fts.search(...)` with BM25,
    highlights, facets, `install_synonyms`, `install_stopwords`
    (plus legacy `fts_index` / `fts_search`).
  - `client.graph.{neighbors_of, bfs_of, shortest_path, k_shortest,
    random_walk, louvain, label_propagation, pagerank, betweenness}`
    plus the legacy kwarg-style `neighbors` / `reverse_neighbors` /
    `bfs` / `path` / `dijkstra`.
- **Frozen-dataclass response models** â€” `SqlSelect`, `SqlInsert`,
  `SqlDelete`, `VectorHit`, `FtsHit`, `Neighbor`, `GraphBfsHit`,
  `GraphPath`, `DijkstraResult` and friends â€” hashable + immutable,
  snake_case field names matching the wire.
- **`OCPaymentRequiredError`** â€” 402 add-on-required mapping. Surfaces
  `addon` / `name` / `monthly_usd` / `preview` / `enterprise_only` /
  `purchase_url` / `msg` as attributes.

### Vector

- `client.vector.delete(table, vec_id)` â€” single delete, end-to-end
- `client.vector.delete_bulk(table, ids)` â€” bulk-delete route (up to
  10 000 ids per call)
- `client.vector.install_centroids(table, centroids)` +
  `train_and_install_centroids(table, partitions, ...)` +
  `centroids(table)` â€” IVF centroid lifecycle
- `client.vector.rebalance_status(table)` â€” IVF rebalance status

### Graph

- `client.graph.node2vec_topk(schema, rel, query_pk, k, metric)` â€”
  over persisted Node2Vec embeddings
- `client.graph.graphsage(schema, feature_col, rel, config)` â€” train +
  optional persist
- `client.graph.graphsage_topk(schema, rel, query_pk, k, metric)` â€”
  over persisted GraphSAGE embeddings

### SQL

- `client.sql.install_materialized_view(name, query, refresh_mode)`
- `client.sql.refresh_materialized_view(name)` â†’
  `{ rows_materialized, bytes_written, refresh_ts }`
- `client.sql.read_materialized_view(name)`

### Admin

- `client.admin.install_tenant_config(tenant_id, replication_mode)`
- `client.admin.get_tenant_config(tenant_id)`

### Usage

- `client.usage()` (sync + async) â€” live usage counters, per-schema
  breakdown, and the tenant's compute configuration. New dataclasses
  `TenantUsage` and `TenantConfiguration`; the `tier` field carries the
  neutral configuration slug (`entry` / `standard` / `advanced` /
  `custom`).

### FTS (no API change â€” behavior upgraded server-side)

- Lemmatization is automatic when the table's analyzer config has
  `lemmatizer="dictionary"`; 9 languages now supported

### Packaging

- `project.urls` (Source / Issues) fixed to point at
  `github.com/originchain-ai/originchain-python` (previously a dead
  repository path)
- Added a `LICENSE` file matching the `Proprietary` license metadata
- Removed committed `__pycache__` artifacts; added `.gitignore`
- HTTP/2 (`h2`) is a hard dependency; the `[http2]` extra is kept as a
  no-op alias for 0.3.x requirements files

### Engine compatibility

- Requires an engine build that contains the IVF-PQ + GraphSAGE +
  materialized views + Raft Phase D commits (any deploy after
  2026-06-08 commit `2c1fe55a`)

### Migration from 0.4.0

- No breaking changes; all new methods are additive
- `client.vector.install_centroids` URL fixed from `install_centroids`
  â†’ `install-centroids` to match the engine's admin-route convention
- Default `User-Agent` is `originchain-python/0.5.0`

## [0.4.0]

- **Note:** 0.4.0 as published contained no functional changes over
  0.3.0 â€” the published artifacts were byte-identical to 0.3.0 apart
  from the version string in the `User-Agent`. The typed-namespace
  work intended for 0.4.0 first ships in 0.5.0.

## 0.3.0

### Changed
- **`vector_topk` `mode` parameter is now `"fast" | "high_recall"`** -
  replaces the previous `"hnsw" | "bruteforce"` value space. The
  parameter is now optional (`mode=None` by default) and is omitted
  from the request body when unset; the server defaults to
  `"high_recall"` when the field is absent. `"fast"` favours latency,
  `"high_recall"` favours recall.
- Default `User-Agent` bumped to `originchain-python/0.3.0`.
