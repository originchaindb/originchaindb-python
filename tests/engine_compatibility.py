"""Private integration worker. Commands arrive over stdin; credentials are never logged."""
import json
import os
import sys

from originchain import Client, OCError

current, calls = "unset", 0


def token():
    global calls
    calls += 1
    return current


with Client(url=os.environ["OC_URL"], token_provider=token) as db:
    for line in sys.stdin:
        command = json.loads(line)
        current = command["token"]
        before, status = calls, 200
        try:
            action = command["action"]
            if action == "query":
                result = db.sql.run(command["sql"], command.get("params", []))
                assert command["status"] == 200
                assert len(result.rows) == command.get("rows", 0)
            elif action == "bulk":
                reports = list(db.rows.write_batches(command["schema"], ({"id": key, "value": index} for index, key in enumerate("abcde")), batch_size=2))
                assert [(r.offset, r.count, r.inserted) for r in reports] == [(0, 2, 2), (2, 2, 2), (4, 1, 1)]
            elif action == "scan":
                assert [row["id"] for row in db.rows.scan(command["schema"], "id", page_size=2)] == list("abcde")
            else:
                raise AssertionError("Unknown compatibility action")
        except OCError as error:
            status = error.status
            assert command["status"] != 200 and status == command["status"], "Unexpected HTTP outcome"
        assert calls - before == command["requests"], "Unexpected request replay or prefetch"
        print(json.dumps({"ok": True, "status": status, "requests": calls - before}), flush=True)
