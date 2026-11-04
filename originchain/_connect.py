"""How a client reaches its database: ``OC_URL`` and ``OC_TOKEN``.

``OC_URL`` is the database's address, copied from Connect. The client uses it
exactly as given and appends a door path (``/sql``, ``/cypher``, ...). It never
builds, edits or parses the address to find a database in it. The one thing it
derives is the origin (scheme, host and port), for the few routes that live
outside the database, such as ``/health``. A trailing ``/`` is ignored so that
appending a door path never produces ``//``.

``OC_TOKEN`` is an access token (``oc_pat_...``) or, on databases where access
tokens are not supported yet, that database's key. It is sent unchanged as
``Authorization: Bearer <OC_TOKEN>``. The client never checks its prefix.

Plain ``http://`` is refused except to this machine (``localhost``,
``127.0.0.0/8``, ``::1``). An address with a user name, password, query or
fragment is refused too.

Names read by earlier releases are still read for one release, with a one-time
:class:`FutureWarning` naming the replacement. They are removed in the next
minor release. ``FutureWarning`` is shown by default wherever the call is made;
a ``DeprecationWarning`` would be hidden unless the call came from ``__main__``,
so an application would meet the removal without notice. This module is the
only place the old names appear.

The ``db_user=`` / ``db_key=`` pair is different: on a database where access
tokens are not supported yet it is the only way to act as a database user, so
it is not scheduled for removal and warns with a ``DeprecationWarning`` only.
"""

from __future__ import annotations

import ipaddress
import threading
import warnings
from typing import Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .errors import OCConfigError

URL_ENV = "OC_URL"
TOKEN_ENV = "OC_TOKEN"

# Read for one release only, each with a one-time FutureWarning.
_OLD_BASE_ENV = "OC_BASE_URL"
_OLD_TENANT_ENV = "OC_TENANT"
_OLD_TOKEN_ENV = "OC_BEARER"

_warned: set[str] = set()
_warned_lock = threading.Lock()


def _warn_once(
    key: str,
    message: str,
    stacklevel: int,
    category: type[Warning] = FutureWarning,
) -> None:
    """Emit ``message`` once per process for ``key``.

    ``stacklevel`` counts from the caller of ``_warn_once``: 1 points at that
    caller, 2 at its caller, and so on. ``key`` is recorded only after
    ``warnings.warn`` returns, so when warnings are turned into errors every
    call raises the same error instead of the first call raising and later
    ones passing. Two threads racing on the first call may both warn."""
    with _warned_lock:
        if key in _warned:
            return
    warnings.warn(message, category, stacklevel=stacklevel + 1)
    with _warned_lock:
        _warned.add(key)


def _reset_warnings_for_tests() -> None:
    with _warned_lock:
        _warned.clear()


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: object, *, name: str = URL_ENV) -> str:
    """Validate the database address and return it, without a trailing ``/``.

    Validation reads the scheme and host only. The path is never interpreted:
    an address with or without a database id in it is used the same way."""
    if not isinstance(url, str) or not url:
        raise OCConfigError(f"{name} is empty: copy your database's address from Connect")
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise OCConfigError(
            f"{name} contains a space, line break or control character: "
            "copy your database's address from Connect exactly as shown"
        )
    if "?" in url or "#" in url:
        raise OCConfigError(
            f"{name} must not contain a query or fragment: use the address from Connect "
            "exactly as shown"
        )
    parts = urlsplit(url)
    if "@" in parts.netloc:
        raise OCConfigError(
            f"{name} must not contain a user name or password: send the access token "
            f"as {TOKEN_ENV} instead"
        )
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in ("https", "http") or not host:
        raise OCConfigError(
            f"{name} must be an https:// address: copy your database's address from Connect"
        )
    try:
        parts.port
    except ValueError:
        raise OCConfigError(f"{name} has an invalid port") from None
    if scheme == "http" and not _is_loopback(host):
        raise OCConfigError(
            f"{name} uses plain http://, which would send {TOKEN_ENV} unencrypted: use the "
            "https:// address from Connect (plain http:// is accepted only for localhost, "
            "127.0.0.1 or ::1)"
        )
    return url.rstrip("/")


def check_token(token: object, *, name: str = TOKEN_ENV) -> str:
    """Validate the token and return it unchanged. Never echoes its value."""
    if not isinstance(token, str) or not token:
        raise OCConfigError(f"{name} is empty: create an access token in Access tokens")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise OCConfigError(
            f"{name} contains a space, line break or non-ASCII character: paste the token "
            "exactly as shown when it was created"
        )
    return token


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` of a checked address, for routes outside the
    database such as ``/health``. Never used to find the database."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _address_from_old_parts(base_url: str, tenant: str) -> str:
    # Earlier releases joined these two values. Kept for one release so that
    # callers who have not moved to OC_URL keep working; nothing new uses it.
    return base_url.rstrip("/") + "/v1/tenants/" + tenant


def resolve_env(environ: Mapping[str, str], *, stacklevel: int) -> Tuple[str, str]:
    """``(url, token)`` from ``OC_URL`` and ``OC_TOKEN``.

    Falls back to the names earlier releases read, warning once for each.
    ``stacklevel`` counts from the caller of this function (1 = that caller)."""
    level = stacklevel + 1
    url: Optional[str] = environ.get(URL_ENV) or None
    token: Optional[str] = environ.get(TOKEN_ENV) or None
    old_base = environ.get(_OLD_BASE_ENV) or None
    old_tenant = environ.get(_OLD_TENANT_ENV) or None
    old_token = environ.get(_OLD_TOKEN_ENV) or None

    if url is None and old_base is not None and old_tenant is not None:
        _warn_once(
            "env:address",
            f"{_OLD_BASE_ENV} and {_OLD_TENANT_ENV} are deprecated and will stop being read "
            f"in the next minor release: set {URL_ENV} to your database's address from Connect",
            level,
        )
        url = _address_from_old_parts(old_base, old_tenant)
    elif url is not None and (old_base is not None or old_tenant is not None):
        _warn_once(
            "env:address-ignored",
            f"{_OLD_BASE_ENV} and {_OLD_TENANT_ENV} are deprecated and ignored because "
            f"{URL_ENV} is set: remove them",
            level,
        )

    if token is None and old_token is not None:
        _warn_once(
            "env:token",
            f"{_OLD_TOKEN_ENV} is deprecated and will stop being read in the next minor "
            f"release: set {TOKEN_ENV} to an access token from Access tokens",
            level,
        )
        token = old_token
    elif token is not None and old_token is not None:
        _warn_once(
            "env:token-ignored",
            f"{_OLD_TOKEN_ENV} is deprecated and ignored because {TOKEN_ENV} is set: remove it",
            level,
        )

    if url is None:
        raise OCConfigError(
            f"missing required env var: {URL_ENV} (your database's address, from Connect)"
        )
    if token is None:
        raise OCConfigError(
            f"missing required env var: {TOKEN_ENV} (an access token, from Access tokens)"
        )
    return check_url(url), check_token(token)


def resolve_args(
    owner: str,
    url: Optional[str],
    token: Optional[str],
    base_url: Optional[str],
    bearer: Optional[str],
    tenant: Optional[str],
    *,
    stacklevel: int,
    credential_optional: bool = False,
) -> Tuple[str, str, Optional[str]]:
    """``(url, token, old_tenant)`` from constructor arguments.

    ``url=`` and ``token=`` are the arguments. ``base_url=``, ``tenant=`` and
    ``bearer=`` still work for one release, warning once. ``old_tenant`` is the
    deprecated ``tenant=`` value, or ``None``."""
    level = stacklevel + 1
    if url is not None and (base_url is not None or tenant is not None):
        raise OCConfigError(
            "pass url= alone: it already names the database (base_url= and tenant= "
            "are deprecated)"
        )
    if token is not None and bearer is not None:
        raise OCConfigError("pass token= alone (bearer= is deprecated)")
    if credential_optional and (token is not None or bearer is not None):
        raise OCConfigError("pass token_provider or token, not both")
    old_tenant: Optional[str] = None
    if url is None:
        if base_url is None and tenant is None:
            raise TypeError(
                f"{owner}() missing required keyword argument: 'url' "
                f"(your database's address, {URL_ENV}, from Connect)"
            )
        if base_url is None or tenant is None:
            raise TypeError(
                f"{owner}(): pass url= with your database's address ({URL_ENV}) from Connect"
            )
        _warn_once(
            "arg:address",
            f"{owner}(base_url=..., tenant=...) is deprecated and will be removed in the next "
            f"minor release: pass url= with your database's address ({URL_ENV}) from Connect",
            level,
        )
        url = _address_from_old_parts(base_url, tenant)
        old_tenant = tenant
    if credential_optional:
        return check_url(url, name="url"), "", old_tenant
    if token is None:
        if bearer is None:
            raise TypeError(
                f"{owner}() missing required keyword argument: 'token' "
                f"(an access token, {TOKEN_ENV})"
            )
        _warn_once(
            "arg:token",
            f"{owner}(bearer=...) is deprecated and will be removed in the next minor "
            f"release: pass token= with an access token ({TOKEN_ENV})",
            level,
        )
        token = bearer
    return check_url(url, name="url"), check_token(token, name="token"), old_tenant


def warn_identity_arguments(owner: str, *, stacklevel: int) -> None:
    """One-time warning for the ``db_user=`` / ``db_key=`` pair.

    Not scheduled for removal: where access tokens are not supported yet, the
    database's key with these two values is the only way to act as a database
    user. So this is a ``DeprecationWarning``, not a ``FutureWarning``."""
    _warn_once(
        "arg:identity",
        f"{owner}(db_user=..., db_key=...) is deprecated: on a database that supports "
        f"access tokens, use one ({TOKEN_ENV}), which carries your database identity. "
        "These arguments keep working on databases where access tokens are not "
        "supported yet",
        stacklevel + 1,
        DeprecationWarning,
    )


def warn_admin_namespace(owner: str, method: str, *, stacklevel: int) -> None:
    """One-time warning for the ``admin`` namespace, removed in the next minor
    release. Its routes sit outside the database's address and take an engine
    tenant id, which an ``OC_URL`` + ``OC_TOKEN`` client never needs."""
    _warn_once(
        "admin:" + method,
        f"{owner}.admin.{method}() is deprecated and will be removed in the next minor "
        f"release: it calls an engine administration route outside your database's "
        f"address ({URL_ENV}), which this client no longer covers",
        stacklevel + 1,
    )
