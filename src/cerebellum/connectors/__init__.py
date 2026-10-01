"""Connectors to external systems. Importing this package registers the built-in types."""

from cerebellum.connectors import postgres as _postgres  # noqa: F401  (registers "postgres")
from cerebellum.connectors import rest as _rest  # noqa: F401  (registers "rest")
from cerebellum.connectors.base import (
    Connector,
    ConnectorEnv,
    ConnectorError,
    ConnectorPool,
    HealthStatus,
    HttpConnector,
    HttpResponse,
    SqlConnector,
    create_connector,
    jsonable,
    register_connector,
)

__all__ = [
    "Connector",
    "ConnectorEnv",
    "ConnectorError",
    "ConnectorPool",
    "HealthStatus",
    "HttpConnector",
    "HttpResponse",
    "SqlConnector",
    "create_connector",
    "jsonable",
    "register_connector",
]
