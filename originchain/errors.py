"""Exception hierarchy for the Python client.

All OC errors derive from :class:`OCError` so callers can catch the base
class. Specific subclasses let callers branch on retryability:

- :class:`OCAuthError` (401 / 403) - DON'T retry, the bearer is wrong.
- :class:`OCPaymentRequiredError` (402) - DON'T retry, an add-on is
  required to call this endpoint.
- :class:`OCNotFoundError` (404) - DON'T retry, the resource doesn't exist.
- :class:`OCValidationError` (400) - DON'T retry, the request is malformed.
- :class:`OCRateLimitedError` (429) - DO retry after ``Retry-After``.
- :class:`OCServerError` (5xx) - MAY retry with backoff if idempotent.
- :class:`OCRecordApiNotEnabledError` (501, a :class:`OCServerError`) -
  DON'T retry, the database does not serve vector index record operations.
- :class:`OCConfigError` - a connection setting was refused before any
  request was sent (for example a plain ``http://`` ``OC_URL``).
- :class:`OCReplicationDegraded` - write succeeded, but sync-replication
  timed out. Set on the response, not raised. Callers can opt to log /
  page on this signal.
"""

from __future__ import annotations

from typing import Any, Optional


class OCError(Exception):
    """Base for every error raised by this client."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body
        #: The engine's id for the request (``X-OC-Request-Id``), when it
        #: answered. Quote it in a support request: it names the exact record.
        self.request_id: str | None = None
        #: The id this client sent for the call (``X-OC-Logical-Request-Id``),
        #: the same on every retry of it.
        self.logical_request_id: str | None = None
        # The structured error envelope:
        # {"error": {"code", "message", "status", "retryable", "details"}}.
        envelope = _envelope(body)
        code = envelope.get("code")
        details = envelope.get("details")
        #: The envelope's machine-readable ``error.code`` (for example
        #: ``"index_already_exists"``), when the engine answered with one.
        self.code: Optional[str] = code if isinstance(code, str) else None
        #: The envelope's ``error.details`` object, when present.
        self.details: Optional[dict[str, Any]] = details if isinstance(details, dict) else None
        reason = self.details.get("reason") if self.details is not None else None
        #: ``error.details.reason`` (for example ``"exact_index_capacity"``).
        #: Branch on ``code`` plus ``reason``, never on ``status`` alone.
        self.reason: Optional[str] = reason if isinstance(reason, str) else None
        retryable = envelope.get("retryable")
        #: ``error.retryable``: ``True`` only when the identical request can
        #: succeed later without changes.
        self.retryable: Optional[bool] = retryable if isinstance(retryable, bool) else None


def _envelope(body: Any) -> dict[str, Any]:
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _error_message(body: Any) -> Any:
    """The message an error is raised with: a flat body's ``error`` text, or
    a structured envelope's ``message`` (else its ``code``)."""
    msg = body.get("error") if isinstance(body, dict) else str(body)
    if isinstance(msg, dict):
        text, code = msg.get("message"), msg.get("code")
        msg = text if isinstance(text, str) and text else code if isinstance(code, str) else None
    return msg


class OCAuthError(OCError):
    """401 / 403. Bearer is missing, wrong, or not scoped to this tenant."""


class OCProtocolError(OCError, ValueError):
    """The server response does not match the supported response contract."""


class OCBatchError(OCError):
    """A SQL batch stopped after a failure. ``result`` retains partial results.

    A batch is not necessarily atomic. Inspect its results before deciding
    whether to retry any statement.
    """

    def __init__(self, result: Any):
        super().__init__("SQL batch contains a failed statement", status=200)
        self.result = result


class OCPaymentRequiredError(OCError):
    """402. The endpoint is gated behind an add-on the tenant hasn't
    purchased. The 402 body shape is:

    .. code-block:: json

        {
          "error": "addon_required",
          "addon": "vector-search",
          "name":  "Vector Search",
          "monthly_usd": 49,
          "preview": false,
          "enterprise_only": false,
          "purchase_url": "https://app.originchain.ai/billing/addons?enable=vector-search",
          "msg": "This endpoint requires the Vector Search add-on. ..."
        }

    Surfaced to callers as attributes so they don't have to dig into
    ``.body`` themselves. ``addon`` / ``name`` / ``monthly_usd`` /
    ``preview`` / ``enterprise_only`` / ``purchase_url`` / ``msg`` are
    populated when the server returns the canonical body and ``None``
    otherwise."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, **kwargs)
        body = self.body if isinstance(self.body, dict) else {}
        self.addon: Optional[str] = body.get("addon")
        self.name: Optional[str] = body.get("name")
        self.monthly_usd: Optional[float] = body.get("monthly_usd")
        self.preview: Optional[bool] = body.get("preview")
        self.enterprise_only: Optional[bool] = body.get("enterprise_only")
        self.purchase_url: Optional[str] = body.get("purchase_url")
        self.msg: Optional[str] = body.get("msg")


class OCNotFoundError(OCError):
    """404. The schema / row / instance doesn't exist."""


class OCValidationError(OCError):
    """400. The request body or query parameters are malformed."""


class OCRateLimitedError(OCError):
    """429. The token's bucket is exhausted. ``retry_after`` is in seconds."""

    def __init__(self, message: str, *, retry_after: float = 1.0, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class OCServerError(OCError):
    """5xx. Server-side failure; retryable for idempotent ops."""


class OCRecordApiNotEnabledError(OCServerError):
    """501 with ``reason == "record_api_not_enabled"``: this database does not
    serve the vector index record operations (``client.vector_indexes``).
    Not retryable: the same request keeps failing until the operations are
    switched on for the database. The per-table ``client.vector`` calls are
    not affected."""


def _server_error_class(status: int, body: Any) -> type[OCServerError]:
    details = _envelope(body).get("details")
    if (
        status == 501
        and isinstance(details, dict)
        and details.get("reason") == "record_api_not_enabled"
    ):
        return OCRecordApiNotEnabledError
    return OCServerError


class OCConfigError(OCError, ValueError):
    """The client was given a connection setting it will not use, such as an
    ``OC_URL`` that is plain ``http://`` to a host other than this machine, or
    an empty ``OC_TOKEN``. Raised before any request is sent. Also a
    :class:`ValueError`, like the client's other option checks."""


class OCReplicationDegraded(Warning):
    """The leader returned 200 but the follower didn't ack within the
    ``--sync-timeout-ms`` window. Surfaced as a warning rather than an
    error because the write IS durable on the leader - a follower lag
    or fence is the real cause and should be paged separately."""


# ── Spec-style aliases ────────────────────────────────────────────────
# The typed-namespace surface (``client.sql.query`` / ``client.vector.*``
# / etc.) documents 4xx errors as ``OriginChainBadRequest`` and 5xx as
# ``OriginChainServerError``. Map both to the existing typed classes so
# `except OriginChainBadRequest` works the same way callers expect from
# the spec, AND `except OCValidationError` keeps working for legacy
# call-sites. Aliases (not subclasses) so `isinstance` checks against
# either name behave identically.

OriginChainBadRequest = OCValidationError
OriginChainServerError = OCServerError
