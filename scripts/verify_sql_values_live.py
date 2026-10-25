"""Verify deployed SQL value contracts with isolated rows, without logging credentials."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid


async def run() -> dict:
    import httpx
    import originchain
    from originchain import AsyncOriginChain, OriginChain

    for name in ("OC_BASE", "OC_TENANT", "OC_BEARER"):
        if not os.environ.get(name):
            raise RuntimeError(f"Missing required live setting: {name}")
    base, tenant, bearer = (os.environ[name] for name in ("OC_BASE", "OC_TENANT", "OC_BEARER"))
    namespace = "py_values_" + uuid.uuid4().hex[:12]
    table = namespace + ".items"
    checks: dict[str, bool] = {}
    observations: dict = {}

    async def call(value):
        return await value if inspect.isawaitable(value) else value

    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + bearer}, timeout=30) as raw:
        # SQL CREATE TABLE's separate response kind is only fixture setup.
        created = raw.post(f"/v1/tenants/{tenant}/sql", json={"sql":
            f"CREATE TABLE {table} (id BIGINT PRIMARY KEY, grp TEXT, amount NUMERIC(10,2), "
            "ts TIMESTAMP, duration INTERVAL)"})
        if not created.is_success:
            raise RuntimeError("Could not create isolated SQL fixture")
        with OriginChain(base_url=base, tenant=tenant, bearer=bearer) as sync:
            try:
                inserted = sync.sql(f"INSERT INTO {table} (id,grp,amount,ts,duration) VALUES "
                    "(1,'g1',12.30,TIMESTAMP '2024-02-29 12:34:56.123456',INTERVAL '36:00:00.000001'),"
                    "(2,'g2',7.25,TIMESTAMP '2024-02-29 12:34:56.123456',INTERVAL '36:00:00.000001'),"
                    "(3,'g1',0.45,TIMESTAMP '2024-02-29 12:34:56.123456',INTERVAL '36:00:00.000001')")
                assert inserted.inserted == 3
                raw_row = raw.get(f"/v1/tenants/{tenant}/rows/{table}/1")
                assert raw_row.is_success
                row_wire = raw_row.json()
                typed_sql = f"SELECT amount,ts,duration FROM {table} WHERE id=1"
                raw_sql = raw.post(f"/v1/tenants/{tenant}/sql", json={"sql": typed_sql})
                assert raw_sql.is_success
                sql_wire = raw_sql.json()
                async with AsyncOriginChain(base_url=base, tenant=tenant, bearer=bearer) as asynchronous:
                    for label, client in [("sync", sync), ("async", asynchronous)]:
                        grouped = await call(client.sql.query(f"SELECT COUNT(*) AS n FROM {table} GROUP BY grp ORDER BY 1"))
                        assert grouped.rows == [{"n": 1}, {"n": 2}] and grouped.columns == ["n"]
                        checks[label + "_unprojected_group_key"] = True
                        sums = await call(client.sql(f"SELECT SUM(amount) AS s FROM {table} GROUP BY grp ORDER BY 1"))
                        assert list(sums.rows) == [{"s": "7.25"}, {"s": "12.75"}]
                        checks[label + "_aggregate_decimal_strings"] = True
                        projected = await call(client.sql.query(f"SELECT grp, COUNT(*) AS n FROM {table} GROUP BY grp ORDER BY grp"))
                        assert projected.rows == [{"grp": "g1", "n": 2}, {"grp": "g2", "n": 1}]
                        checks[label + "_projected_group_key"] = True
                        temporal = await call(client.sql.query("SELECT TIMESTAMP '2024-02-29 12:34:56.123456' AS ts, INTERVAL '36:00:00.000001' AS duration"))
                        assert temporal.rows == [{"duration": "36:00:00.000001", "ts": "2024-02-29 12:34:56.123456"}]
                        assert temporal.column_types and temporal.column_types.get("duration") == "interval"
                        checks[label + "_microsecond_sql_literals"] = True
                        typed = await call(client.sql.query(typed_sql))
                        assert typed.rows == sql_wire["rows"]
                        assert typed.column_types == sql_wire["column_types"]
                        assert typed.column_types["amount"] == "numeric" and typed.rows[0]["amount"] == "12.30"
                        checks[label + "_sql_type_metadata_and_exact_cells"] = True
                        tagged = await call(client.sql(typed_sql))
                        assert list(tagged.rows) == sql_wire["rows"] and tagged.column_types == sql_wire["column_types"]
                        checks[label + "_tagged_metadata"] = True
                        row = await call(client.rows.get(table, "1"))
                        assert row == row_wire
                        checks[label + "_row_json_unchanged"] = True
                observations["stored_timestamp_wire_type"] = type(row_wire["ts"]).__name__
                observations["stored_microsecond_timestamp_verified"] = row_wire["ts"] == "2024-02-29T12:34:56.123456Z"
                observations["stored_microsecond_interval_verified"] = row_wire["duration"] == "36:00:00.000001"
                special = raw.post(f"/v1/tenants/{tenant}/sql", json={"sql": "SELECT CAST('NaN' AS DOUBLE PRECISION) AS value"})
                observations["floating_special_cast_status"] = special.status_code
                observations["server_backed_floating_special_conversion_verified"] = False
            finally:
                sync.sql(f"DELETE FROM {table}")
    return {"passed": True, "version": originchain.__version__, "checks": checks,
            "observations": observations, "scope": "Unique table with synthetic rows only"}


def main() -> None:
    try:
        result = asyncio.run(run())
    except Exception as error:
        print(json.dumps({"passed": False, "error_class": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
