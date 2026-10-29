"""Read-only examples against an existing OriginChainDB database."""
import argparse
import dataclasses
import json
import math
import os
import re

from originchain import OriginChain


def required(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name}")
    return value


def identifier(name):
    value = required(name)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", value):
        raise ValueError(f"{name} must be an identifier, optionally namespace-qualified")
    return value


def encode(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["sql", "fts", "vector", "graph"])
    operation = parser.parse_args().operation
    settings = {"timeout": 30, "max_retries": 0}
    if "OC_DB_USER" in os.environ or "OC_DB_KEY" in os.environ:
        settings.update(db_user=required("OC_DB_USER"), db_key=required("OC_DB_KEY"))
    with OriginChain.from_env(**settings) as db:
        if operation == "sql":
            result = db.sql.query("SELECT $1 AS message", ["Hello from OriginChainDB"])
        elif operation == "fts":
            result = db.fts.search(identifier("OC_TABLE"), identifier("OC_FTS_FIELD"),
                                   required("OC_QUERY"), mode="bm25", k=5)
        elif operation == "vector":
            query = json.loads(required("OC_QUERY_VECTOR"))
            if (not isinstance(query, list) or not query
                    or not all(type(x) in (float, int) and math.isfinite(x) for x in query)):
                raise ValueError("OC_QUERY_VECTOR must be a nonempty JSON array of finite numbers")
            result = db.vector.topk(identifier("OC_TABLE"), query, k=5, metric="cosine")
        else:
            result = db.graph.neighbors(identifier("OC_TABLE"),
                                        rel=identifier("OC_RELATION"), pk=required("OC_NODE_ID"))
        print(json.dumps(result, default=encode, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Query failed. Check connection, permissions and example inputs.") from None
