"""Typed rows, a credential provider, native HTTP transport, and local metrics."""
import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from originchain import Client

@dataclass
class Product:
    id: str
    name: str

def product(row: Mapping[str, Any]) -> Product:
    if not isinstance(row.get("id"), str) or not isinstance(row.get("name"), str):
        raise ValueError("Product requires string id and name")
    return Product(id=row["id"], name=row["name"])

# A deployed application can replace this provider with its credential cache.
# Configure proxy or TLS roots on HTTPTransport if required. The SDK owns it.
with Client(url=os.environ["OC_URL"], token_provider=lambda: os.environ["OC_TOKEN"],
            transport=httpx.HTTPTransport(),
            on_request=lambda event: print(event.operation, event.status, event.duration)) as db:
    result = db.sql.run("SELECT $1 AS id, $2 AS name", ["p1", "Widget"])
    print(result.map_rows(product))
