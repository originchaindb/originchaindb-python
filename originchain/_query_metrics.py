"""Wire-shaped query metrics shared by the synchronous and async clients."""

from typing import List, Optional

from typing_extensions import TypedDict


class QueryMetricsBucket(TypedDict):
    """One bucket; ``at`` is Unix milliseconds and idle latency is ``None``.

    ``observed_seconds`` is the covered part of the bucket. Divide ``calls``
    by this value, rather than the requested bucket size, for throughput.
    """

    at: int
    calls: int
    failed: int
    mean_latency_ms: Optional[float]
    observed_seconds: float


# Functional syntax retains the wire's ``from`` key, a Python keyword.
QueryMetricsResponse = TypedDict("QueryMetricsResponse", {
    "schema_version": int,
    "tenant_id": str,
    "from": int,
    "to": int,
    "bucket_seconds": int,
    "collection_started_at": int,
    "scope": str,
    "retention_seconds": int,
    "buckets": List[QueryMetricsBucket],
    "dropped_events": int,
})


def _query_metrics_params(minutes: int, bucket_seconds: int) -> dict[str, int]:
    for name, value in (("minutes", minutes), ("bucket_seconds", bucket_seconds)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    # The server validates supported pairs; leave future windows available to
    # callers without requiring an SDK update.
    return {"minutes": minutes, "bucket_seconds": bucket_seconds}
