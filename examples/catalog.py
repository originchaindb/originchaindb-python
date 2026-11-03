from pathlib import Path
from originchain import Client, OCBatchError, OCError


def main():
    with Client.from_env() as db:
        # section:setup
        schema = "sdk_examples.products"
        toml = Path(__file__).with_name("products.toml").read_text(encoding="utf-8")
        db.schemas.register(toml)
        # endsection

        # section:rows
        db.rows.put(schema, {"id": "p2", "name": "Gadget", "description": "A red gadget"})
        db.rows.put(
            schema,
            {
                "id": "p1",
                "name": "Widget",
                "description": "A small blue widget",
                "related_to": "p2",
            },
        )
        product = db.rows.get(schema, "p1")
        print(product)
        # endsection

        # section:sql
        result = db.sql.run("SELECT id, name FROM sdk_examples.products WHERE id = $1", ["p1"])
        for row in result.rows:
            print(row["id"], row["name"])
        # endsection

        # section:vector
        embedding = [0.1, 0.2, 0.3, 0.4]
        db.vector.put(schema, "p1", embedding, {"category": "tools"})
        hits = db.vector.search(schema, embedding, k=5)
        for hit in hits:
            print(hit.id, hit.score)
        # endsection

        # section:fts
        db.fts.index(schema, "description", "p1", "A small blue widget")
        matches = db.fts.search(schema, "description", "blue widget", k=5)
        for hit in matches.hits:
            print(hit.doc_id, hit.score)
        # endsection

        # section:graph
        neighbors = db.graph.neighbors(schema, pk="p1", rel="related")
        print(neighbors)
        # endsection


if __name__ == "__main__":
    try:
        main()
    except OCBatchError as error:
        print("Batch failed; inspect partial results:", len(error.result.results))
        raise
    except OCError as error:
        print("Request failed:", error.status, error.request_id)
        raise
