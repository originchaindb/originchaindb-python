# Changelog

## 1.0.0-rc.1 — unreleased

- Handwritten language-native clients around the shared database contract.
- Connect using `OC_URL` and `OC_TOKEN`.
- Consistent SQL, schema, row, vector, full-text and graph namespaces.
- Complete SQL results and explicit partial-batch errors.
- Encoded resource paths, no automatic write retries, and no redirect following.
- Shared HTTP conformance tests and source-only CI.

- Application-managed rotating credential providers and local redacted request observers.
- Native transport configuration and typed row conversion with explicit conversion errors.
- Shared failure tests, pinned cross-language contract, versioned API and migration guides.
- Complete application integration example and manually dispatched live verification.

- Lazy bounded batch writes with acknowledged ranges and explicit unknown outcomes.
- Keyset pages and row iteration over unique immutable string keys, with native cancellation.
- Three-workload transport benchmarks, reviewed CI baselines and public API compatibility guards.
- Fresh application installation checks and signed CI artifact provenance.
- Complete bulk/read/resume example and detailed recovery guidance.

- Vector indexes on `vector_indexes` (sync and async): index create, describe, list,
  configure, delete and statistics; namespaces; batched upsert with acknowledged ranges;
  fetch, fetch by filter, list ids and count; update and delete by id, by filter or all;
  jobs; dense, sparse, keyword and hybrid search with metadata filters.
- Structured error envelopes populate `code`, `reason`, `details` and `retryable` on
  `OCError`, and the error message is the envelope's message.
- `OCRecordApiNotEnabledError` for a database that does not serve vector indexes.

This candidate has not been published to package registries.
