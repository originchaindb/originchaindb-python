"""Native async transport benchmark; the harness supplies a loopback endpoint."""
import asyncio
import json
import time

from originchain import AsyncClient


async def main():
    report = {}
    async with AsyncClient.from_env() as db:
        async def query(mode):
            start = time.perf_counter()
            result = await db.sql.query("bench:" + mode)
            assert len(result.rows) == (2048 if mode == "large" else 1)
            return (time.perf_counter() - start) * 1000

        for _ in range(10):
            await query("warm")
        for mode, count in [("sequential", 100), ("concurrent", 64), ("large", 10)]:
            start = time.perf_counter()
            if mode == "concurrent":
                limit = asyncio.Semaphore(8)

                async def worker():
                    async with limit:
                        return await query(mode)

                values = await asyncio.gather(*(worker() for _ in range(count)))
            else:
                values = [await query(mode) for _ in range(count)]
            report[mode + "_ms"] = values
            report[mode + "_rps"] = count / (time.perf_counter() - start)
    print("OC_BENCHMARK " + json.dumps(report))


asyncio.run(main())
