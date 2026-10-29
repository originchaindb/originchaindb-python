"""Install the built wheel in a fresh venv and check its public async surface."""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def verify_installed() -> None:
    import httpx
    import originchain
    from originchain import AsyncOriginChain, OriginChain

    package = Path(originchain.__file__).resolve().parent
    assert package.is_relative_to(Path(sys.prefix).resolve()), "imported source checkout"
    assert (package / "py.typed").is_file(), "missing PEP 561 marker"
    assert (package / "_async_namespaces.py").is_file(), "missing async namespaces"
    from originchain import QueryMetricsBucket, QueryMetricsResponse
    assert QueryMetricsBucket.__required_keys__ and QueryMetricsResponse.__required_keys__
    assert not hasattr(OriginChain, "ask")
    assert not hasattr(AsyncOriginChain, "ask")
    requests = []
    metrics = {
        "schema_version": 1, "tenant_id": "test", "from": 1790000000000,
        "to": 1790000060000, "collection_started_at": 1790000000000,
        "scope": "local-process", "bucket_seconds": 60, "retention_seconds": 604800,
        "dropped_events": 4294967300, "buckets": [
            {"at": 1790000000000, "calls": 0, "failed": 0,
             "mean_latency_ms": None, "observed_seconds": 60.0},
            {"at": 1790000060000, "calls": 4294967300, "failed": 4294967299,
             "mean_latency_ms": 12.5, "observed_seconds": 60.0},
        ],
    }

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/query-metrics"):
            assert dict(request.url.params) == {"minutes": "60", "bucket_seconds": "60"}
            return httpx.Response(200, json=metrics)
        if request.url.path.endswith("/_batch"):
            return httpx.Response(200, json={"inserted": len(json.loads(request.content))})
        if request.url.path.endswith("/sql"):
            if json.loads(request.content)["sql"] == "SELECT compatibility_values":
                return httpx.Response(200, json={"kind": "select", "columns": ["n", "ts", "value", "amount"],
                    "rows": [{"n": 2, "ts": "2024-02-29 12:34:56.123456", "value": "NaN", "amount": "Infinity"}],
                    "column_types": {"ts": "timestamp", "value": "float8", "amount": "numeric"}})
            if json.loads(request.content)["sql"] == "DELETE FROM demo.rows":
                return httpx.Response(200, json={
                    "kind": "delete", "schema": "demo.rows", "rows_affected": 2,
                })
            if json.loads(request.content)["sql"].startswith("DELETE"):
                return httpx.Response(200, json={
                    "kind": "delete", "schema": "demo.rows", "pk": "one", "rows_affected": 1,
                })
            return httpx.Response(200, json={"kind": "select", "rows": [{"id": "one"}]})
        if request.url.path.endswith("/delete-bulk"):
            return httpx.Response(200, json={"deleted_count": 1, "missing_count": 0})
        if request.url.path.endswith("/k-shortest"):
            return httpx.Response(200, json={"paths": [{"nodes": ["one", "two"], "cost": 1}]})
        if "/fts/" in request.url.path:
            return httpx.Response(200, json=[{"doc_id": "one", "score": 1}])
        return httpx.Response(200, json={"replication_mode": "active_passive"})

    real_sync = httpx.Client
    httpx.Client = lambda **kwargs: real_sync(**kwargs, transport=httpx.MockTransport(handler))
    try:
        with OriginChain(base_url="https://engine.invalid", bearer="test", tenant="test", verify=False) as client:
            assert client.query_metrics() == metrics
            deleted = client.sql("DELETE FROM demo.rows")
            assert deleted.pk is None and deleted.rows_affected == 2
            assert client.sql.execute("DELETE FROM demo.rows").rows_affected == 2
            result = client.sql.query("SELECT compatibility_values")
            assert result.column_types == {"ts": "timestamp", "value": "float8", "amount": "numeric"}
            assert set(result.rows[0]) == {"n", "ts", "value", "amount"}
            assert result.rows[0]["ts"] == "2024-02-29 12:34:56.123456"
            assert result.rows[0]["amount"] == "Infinity" and math.isnan(result.rows[0]["value"])
    finally:
        httpx.Client = real_sync
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler))

    async def smoke():
        async with AsyncOriginChain(
            base_url="https://engine.invalid", bearer="test", tenant="test", verify=False,
        ) as db:
            assert await db.query_metrics() == metrics
            deleted = await db.sql("DELETE FROM demo.rows")
            assert deleted.pk is None and deleted.rows_affected == 2
            assert (await db.sql.execute("DELETE FROM demo.rows")).rows_affected == 2
            result = await db.sql("SELECT compatibility_values")
            assert result.column_types == {"ts": "timestamp", "value": "float8", "amount": "numeric"}
            assert set(result.rows[0]) == {"n", "ts", "value", "amount"}
            assert result.rows[0]["ts"] == "2024-02-29 12:34:56.123456"
            assert result.rows[0]["amount"] == "Infinity" and math.isnan(result.rows[0]["value"])
            assert (await db.sql("SELECT * FROM demo.rows")).rows == ({"id": "one"},)
            assert (await db.sql.query("SELECT * FROM demo.rows")).rows == [{"id": "one"}]
            assert (await db.sql.execute("DELETE FROM demo.rows WHERE id='one'")).rows_affected == 1
            assert await db.rows.put_batch("demo.rows", [{"id": "one"}], chunk=1) == 1
            assert (await db.vector.delete_bulk("embeddings", ["one"])).deleted_count == 1
            assert (await db.fts.search("docs", "text", "example")).hits[0].doc_id == "one"
            assert await db.graph.shortest_path("nodes", "one", "two", "follows") == ["one", "two"]
            assert (await db.admin.get_tenant_config("test")).replication_mode == "active_passive"
        assert db._client.is_closed

    try:
        asyncio.run(smoke())
    finally:
        httpx.AsyncClient = real
    assert len(requests) == 16
    print("Fresh wheel: typing marker, async namespaces, SQL value metadata, metrics and bulk DELETE passed")


def main() -> None:
    if sys.argv[1:] == ["--installed"]:
        verify_installed()
        return
    root = Path(__file__).resolve().parents[1]
    wheels = [Path(sys.argv[1]).resolve()] if len(sys.argv) == 2 else list((root / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one built wheel in dist/")
    if not wheels[0].is_file() or wheels[0].suffix != ".whl":
        raise SystemExit("Expected a wheel file")
    with tempfile.TemporaryDirectory(prefix="originchain-wheel-") as tmp:
        env = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(env)
        python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])], check=True, cwd=tmp)
        subprocess.run([str(python), "-I", str(Path(__file__).resolve()), "--installed"],
                       check=True, cwd=tmp)
        subprocess.run([str(python), "-I", str(root / "tests" / "test_examples.py")],
                       check=True, cwd=tmp)


if __name__ == "__main__":
    main()
