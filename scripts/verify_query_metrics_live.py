"""Read-only live acceptance; no query history, tenant IDs or secrets are printed."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from originchain import AsyncOriginChain, OriginChain, QueryMetricsResponse


def verify(value: QueryMetricsResponse, tenant: str, seconds: int) -> int:
    assert value["schema_version"] == 1
    assert value["tenant_id"].upper() == tenant.upper()
    assert value["scope"] == "local-process"
    assert value["bucket_seconds"] == seconds
    assert value["retention_seconds"] == 604800
    assert value["dropped_events"] == 0
    for field in ("from", "to", "collection_started_at"):
        assert type(value[field]) is int and value[field] > 2**31
    assert value["to"] >= value["from"]
    assert value["buckets"]
    for bucket in value["buckets"]:
        assert type(bucket["at"]) is int and bucket["at"] > 2**31
        assert type(bucket["calls"]) is int and bucket["calls"] >= 0
        assert type(bucket["failed"]) is int and 0 <= bucket["failed"] <= bucket["calls"]
        assert bucket["observed_seconds"] > 0
        if bucket["calls"] == 0:
            assert bucket["mean_latency_ms"] is None
        else:
            assert bucket["mean_latency_ms"] is not None and bucket["mean_latency_ms"] >= 0
    # A freshly started or continuously busy process may have no idle bucket.
    # Report observed null cases without fabricating one or failing valid data.
    return sum(b["calls"] == 0 and b["mean_latency_ms"] is None for b in value["buckets"])


def main() -> int:
    if not __debug__:
        raise RuntimeError("Live assertions require a non-optimized Python interpreter")
    options = {"base_url": os.environ["OC_BASE"], "bearer": os.environ["OC_BEARER"],
               "tenant": os.environ["OC_TENANT"]}
    # This endpoint requires service/admin credentials. Do not use impersonated
    # database-user headers or bypass a server refusal.
    checks = {}
    observed_idle_null_buckets = {}
    with OriginChain(**options) as client:
        for minutes, seconds in ((60, 60), (1440, 600), (10080, 3600)):
            name = f"sync_{minutes}_{seconds}"
            observed_idle_null_buckets[name] = verify(
                client.query_metrics(minutes, seconds), options["tenant"], seconds,
            )
            checks[name] = True

    async def run_async():
        async with AsyncOriginChain(**options) as client:
            for minutes, seconds in ((60, 60), (1440, 600), (10080, 3600)):
                name = f"async_{minutes}_{seconds}"
                observed_idle_null_buckets[name] = verify(
                    await client.query_metrics(minutes, seconds), options["tenant"], seconds,
                )
                checks[name] = True

    asyncio.run(run_async())
    print(json.dumps({"query_metrics_live": "passed", "checks": checks,
                      "observed_idle_null_buckets": observed_idle_null_buckets}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Error bodies may include operational details. Preserve failure without
        # dumping the response, tenant or secret-bearing environment.
        print(json.dumps({"query_metrics_live": "failed", "error_type": type(error).__name__}))
        sys.exit(1)
