"""Database identity headers. These supplement the instance bearer token."""
from typing import Optional


def identity_headers(db_user: Optional[str], db_key: Optional[str]) -> dict[str, str]:
    if db_user is None and db_key is None:
        return {}
    if any(not isinstance(v, str) or not v.strip() or any(ord(c) < 32 or ord(c) > 126 for c in v)
           for v in (db_user, db_key)):
        raise ValueError("db_user and db_key must both be non-empty printable ASCII header values")
    assert db_user is not None and db_key is not None
    return {"X-OC-DB-User": db_user, "X-OC-DB-Key": db_key}
