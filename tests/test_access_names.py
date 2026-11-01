"""Guard: the docs, the examples and the package teach only OC_URL + OC_TOKEN.

The lists are the connect contract's ``never_access`` and ``wire_terms``
(one-access-path plan, section 2.1). Matching is exact and case-sensitive, the
same as ``grep -F`` without ``-i``.

Two files are exempt because they keep deprecated names working for one
release: ``_connect.py`` reads the old environment names and ``_identity.py``
sends the old identity headers. The negative control proves the exemption,
not a failed read, is what lets them pass.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# TODO(connect contract): read these from operation-backend
# src/connect/contract.v1.json once it lands (branch feat/one-access-contract).
NEVER_ACCESS = [
    "ORIGINCHAIN_HOST", "ORIGINCHAIN_PORT", "ORIGINCHAIN_USER", "ORIGINCHAIN_PASSWORD",
    "ORIGINCHAIN_DB", "ORIGINCHAIN_TOKEN", "ORIGINCHAIN_URL", "ORIGINCHAIN_TENANT",
    "ORIGINCHAIN_DB_USER", "ORIGINCHAIN_CA", "OC_HOST", "OC_TENANT", "OC_BASE_URL",
    "OC_BEARER", "OC_API_KEY", "OC_ENDPOINT", "OC_DB_USER", "OC_DB_KEY", "OC_DB_PASSWORD",
    "OC_ES_URL", "X-OC-DB-User", "x-oc-db-user", "X-OC-DB-Key", "x-oc-db-key", "X-OC-Tenant",
    "/v1/tenants/$", "/v1/tenants/{tenant}", "/v1/tenants/YOUR_TENANT", "oc_live_",
]
WIRE_TERMS = ["psycopg", "node-postgres", "postgresql://", "PGPASSWORD", ":5432", "sslmode"]
BANNED = NEVER_ACCESS + WIRE_TERMS

EXEMPT = {
    "originchain/_connect.py": "reads the old environment names for one release",
    "originchain/_identity.py": "sends the deprecated identity headers for one release",
}


def guarded_files() -> list[str]:
    names = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "RELEASE_NOTES.md", "RELEASING.md"]
    for folder, pattern in (("examples", "*"), ("validation", "*"), ("originchain", "*.py")):
        names += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).glob(pattern)
                        if p.is_file())
    return names


def banned_terms_in(text: str) -> list[str]:
    return [term for term in BANNED if term in text]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_docs_examples_and_package_name_only_the_one_access_path():
    checked = [name for name in guarded_files() if name not in EXEMPT]
    # Positive control on the read itself: the files are there and readable.
    assert {"README.md", "examples/query.py", "originchain/client.py"} <= set(checked)
    assert "OC_URL" in read("README.md") and "OC_TOKEN" in read("README.md")
    found = {name: banned_terms_in(read(name)) for name in checked}
    assert {name: terms for name, terms in found.items() if terms} == {}


def test_guard_flags_every_banned_term():
    """Positive control: a fixture naming each term is flagged for each term."""
    fixture = "\n".join(f"export {term}=x" for term in BANNED)
    assert banned_terms_in(fixture) == BANNED
    for term in BANNED:
        assert banned_terms_in(f"see {term} here") != [], term


def test_guard_is_case_sensitive_like_grep_F():
    # Control for the matcher: a case-changed name is not the banned name.
    assert banned_terms_in("oc_base_url") == []
    assert banned_terms_in("OC_BASE_URL") == ["OC_BASE_URL"]


def test_exempt_files_would_be_flagged_without_their_exemption():
    """Negative control: the exempt shims do contain old names, so passing the
    main check is the exemption at work, not an empty read."""
    for name in EXEMPT:
        assert name in guarded_files()
        assert banned_terms_in(read(name)) != [], name
