"""OriginChain Python client.

Two clients ship in this package:

- :class:`OriginChain` - synchronous, suitable for scripts and Jupyter.
- :class:`AsyncOriginChain` - asyncio, suitable for ASGI / async data
  pipelines.

Both reach the database over HTTPS with two values: ``OC_URL`` (your
database's address, from Connect) and ``OC_TOKEN`` (an access token).
Each call appends its path (``/sql``, ``/cypher``, ...) to ``OC_URL``
exactly as given and sends ``Authorization: Bearer $OC_TOKEN``. They
do not retry requests by default.

Quick start::

    from originchain import OriginChain

    db = OriginChain.from_env()                  # OC_URL + OC_TOKEN
    db.schemas.register(open("orders.toml").read())
    db.rows.put("trading.orders", {"order_id": "o1", "symbol": "AAPL", "qty": 100})
    rows = db.sql.query("SELECT * FROM trading.orders WHERE symbol=$1 AND qty>$2", ["AAPL", 50]).rows

The four substrate-extension surfaces (SQL, vector, full-text, graph)
are also typed first-class. See :class:`OriginChain.sql`,
``vector_topk`` / ``vector_put``, ``fts_search``, and the ``graph``
namespace.
"""

from .iteration import BatchResult, BatchWriteError, RowPage
from .integration import RequestEvent, OCCredentialError, RowDecodeError, map_rows
from ._query_metrics import QueryMetricsBucket, QueryMetricsResponse
from .client import OriginChain
from .async_client import AsyncOriginChain

from .errors import (
    OCAuthError,
    OCBatchError,
    OCProtocolError,
    OCConfigError,
    OCError,
    OCNotFoundError,
    OCPaymentRequiredError,
    OCRateLimitedError,
    OCReplicationDegraded,
    OCServerError,
    OCValidationError,
    OriginChainBadRequest,
    OriginChainServerError,
)
from .models import (
    CentroidsPreview,
    DijkstraResult,
    FacetBucket,
    FtsHit,
    FtsHitWithHighlights,
    FtsResult,
    GraphBfsHit,
    GraphEmbeddingHit,
    GraphPath,
    GraphSageResult,
    InstallCentroidsResult,
    IvfRebalanceStatus,
    MaterializedViewInstallResult,
    MaterializedViewRefreshResult,
    MaterializedViewRows,
    Neighbor,
    Path,
    SqlDelete,
    SqlExecResult,
    SqlInsert,
    SqlResponse,
    SqlResult,
    SqlSelect,
    TenantConfigSnapshot,
    TenantConfiguration,
    TenantUsage,
    TrainAndInstallCentroidsResult,
    VectorDeleteBulkResult,
    VectorDeleteResult,
    VectorHit,
    VectorHitV2,
)

# Short names for new applications; established names remain supported.
Client = OriginChain
AsyncClient = AsyncOriginChain

__all__ = [
    "BatchResult", "BatchWriteError", "RowPage",
    "RequestEvent", "OCCredentialError", "RowDecodeError", "map_rows",
    "Client",
    "AsyncClient",
    "OriginChain",
    "AsyncOriginChain",
    # Errors
    "OCError",
    "OCAuthError",
    "OCBatchError",
    "OCProtocolError",
    "OCConfigError",
    "OCNotFoundError",
    "OCPaymentRequiredError",
    "OCRateLimitedError",
    "OCServerError",
    "OCValidationError",
    "OCReplicationDegraded",
    "OriginChainBadRequest",
    "OriginChainServerError",
    # Models
    "QueryMetricsBucket",
    "QueryMetricsResponse",
    "SqlSelect",
    "SqlInsert",
    "SqlDelete",
    "SqlResponse",
    "SqlResult",
    "SqlExecResult",
    "VectorHit",
    "VectorHitV2",
    "FtsHit",
    "FtsHitWithHighlights",
    "FtsResult",
    "FacetBucket",
    "Neighbor",
    "GraphBfsHit",
    "GraphPath",
    "DijkstraResult",
    "Path",
    "InstallCentroidsResult",
    # 0.5 additions
    "VectorDeleteResult",
    "VectorDeleteBulkResult",
    "IvfRebalanceStatus",
    "TrainAndInstallCentroidsResult",
    "CentroidsPreview",
    "GraphEmbeddingHit",
    "GraphSageResult",
    "MaterializedViewInstallResult",
    "MaterializedViewRefreshResult",
    "MaterializedViewRows",
    "TenantConfigSnapshot",
    # weather-name removal
    "TenantConfiguration",
    "TenantUsage",
]

__version__ = "1.0.0rc1"
