"""Run against a development database; writes sdk_iteration.items."""
from pathlib import Path
from originchain import Client

with Client.from_env() as db:
    schema = "sdk_iteration.items"
    db.schemas.register(Path(__file__).with_name("items.toml").read_text(encoding="utf-8"))
    rows = ({"id": key, "name": f"Item {key}"} for key in "abcde")
    # section:bulk
    for batch in db.rows.write_batches(schema, rows, batch_size=2):
        print(batch.offset, batch.count, batch.inserted)
    # endsection
    # section:scan
    for row in db.rows.scan(schema, "id", page_size=2):
        print(row["id"], row["name"])
    # endsection
    # Resume after a previously processed key. Save a checkpoint only after
    # processing every row in its page successfully.
    for page in db.rows.pages(schema, "id", page_size=2, after="b"):
        for row in page.rows:
            print("resumed", row["id"])
        print("next cursor", page.next_cursor)
