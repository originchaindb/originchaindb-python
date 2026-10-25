# Development verification

Verified 25 October 2026. This record describes development source, not a new
release or package-registry installation.

- Client: **Python**, package `originchain`.
- Tested public runtime: [`944cd410ffb1`](https://github.com/originchaindb/originchaindb-python/tree/944cd410ffb11110c00ce925c0c609e11862916b).
- Source version: `0.11.0.dev0`; follow this repository's README to install it.
- Installation: a newly built package installed into a separate consumer.
- Database: an ephemeral test engine, with isolated test data; no production rollout.

## Passed operations

SQL with bound parameters, graph traversal, vector search, boolean full-text
search, BM25 ranked hits, highlighted search results, diagnostic requests and
query metrics. Metric checks include an idle bucket with null mean latency.

The installed package also passed SQL output-type checks for floating-point
special values. Both synchronous and asynchronous Python clients converted
explicitly typed float special values while preserving ordinary text and nulls.

## Limits

These results are representative operation checks, not a guarantee for every
server version, database tier, permission configuration or input. Database
features still need server support. New microsecond-timestamp and calendar-
interval storage formats remain disabled while compatibility work continues.

The build-time receipts retained elsewhere in this repository keep their
original passed/skipped stages. Later acceptance is recorded here rather than
changing an older release's evidence. Releases and registry publication remain
paused; installing a package by name may select an older version.

See the [SDK support matrix](https://github.com/originchaindb/.github/blob/main/SDK-SUPPORT.md) for other clients and runtime coverage.
