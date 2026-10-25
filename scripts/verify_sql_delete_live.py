"""Exercise bulk DELETE using only new, uniquely named fixture tables."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from originchain import AsyncOriginChain, OriginChain, __version__


def schema(namespace: str, table: str) -> str:
    return (
        f'namespace = "{namespace}"\ntable = "{table}"\nprimary_key = ["id"]\n'
        '[[columns]]\nname = "id"\nty = "str"\nrequired = true\n'
    )


def sync_checks(options: dict, namespace: str, checks: dict) -> None:
    name = namespace + ".sync_items"
    with OriginChain(**options) as client:
        client.schemas.register(schema(namespace, "sync_items"))
        try:
            client.rows.put(name, {"id": "first"})
            client.rows.put(name, {"id": "second"})
            assert len(client.sql.query(f"SELECT id FROM {name}").rows) == 2
            deleted = client.sql(f"DELETE FROM {name}")
            assert deleted.pk is None and deleted.rows_affected == 2
            assert client.sql.query(f"SELECT id FROM {name}").rows == []
            checks["sync_tagged_bulk_delete"] = True
            client.rows.put(name, {"id": "first"})
            client.rows.put(name, {"id": "second"})
            executed = client.sql.execute(f"DELETE FROM {name}")
            assert executed.rows_affected == 2
            assert client.sql.query(f"SELECT id FROM {name}").rows == []
            checks["sync_execute_bulk_delete"] = True
            assert client.sql(f"DELETE FROM {name}").rows_affected == 0
            checks["sync_empty_delete_count"] = True
        finally:
            client.sql(f"DELETE FROM {name}")


async def async_checks(options: dict, namespace: str, checks: dict) -> None:
    name = namespace + ".async_items"
    async with AsyncOriginChain(**options) as client:
        await client.schemas.register(schema(namespace, "async_items"))
        try:
            await client.rows.put(name, {"id": "first"})
            await client.rows.put(name, {"id": "second"})
            assert len((await client.sql.query(f"SELECT id FROM {name}")).rows) == 2
            deleted = await client.sql(f"DELETE FROM {name}")
            assert deleted.pk is None and deleted.rows_affected == 2
            assert (await client.sql.query(f"SELECT id FROM {name}")).rows == []
            checks["async_tagged_bulk_delete"] = True
            await client.rows.put(name, {"id": "first"})
            await client.rows.put(name, {"id": "second"})
            executed = await client.sql.execute(f"DELETE FROM {name}")
            assert executed.rows_affected == 2
            assert (await client.sql.query(f"SELECT id FROM {name}")).rows == []
            checks["async_execute_bulk_delete"] = True
            assert (await client.sql(f"DELETE FROM {name}")).rows_affected == 0
            checks["async_empty_delete_count"] = True
        finally:
            await client.sql(f"DELETE FROM {name}")


def run() -> dict:
    options = {
        "base_url": os.environ["OC_BASE"],
        "tenant": os.environ["OC_TENANT"],
        "bearer": os.environ["OC_BEARER"],
        "max_retries": 1,
    }
    namespace = "sdk_delete_" + uuid.uuid4().hex[:12]
    checks: dict[str, bool] = {}
    sync_checks(options, namespace, checks)
    asyncio.run(async_checks(options, namespace, checks))
    assert len(checks) == 6
    return {"passed": True, "version": __version__, "checks": checks,
            "scope": "New uniquely named tables with synthetic fixture rows only"}


if __name__ == "__main__":
    try:
        print(json.dumps(run()))
    except Exception as error:
        print(json.dumps({"passed": False, "error_class": type(error).__name__,
                          "http_status": getattr(error, "status", None)}))
        raise SystemExit(1) from None
