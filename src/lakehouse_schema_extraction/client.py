"""Trino/Starburst connection handling and native-SQL pass-through."""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from typing import Any

import trino

DEFAULT_HOST = os.environ.get("LAKEHOUSE_HOST", "lakehouse-pov.jgi.lbl.gov")
DEFAULT_PORT = int(os.environ.get("LAKEHOUSE_PORT", "443"))


def _auth():
    """Basic auth when a password is in the environment, otherwise OAuth2 browser flow."""
    password = os.environ.get("TRINO_PASSWORD")
    if password:
        return trino.auth.BasicAuthentication(
            os.environ.get("TRINO_USER", getpass.getuser()), password
        )
    return trino.auth.OAuth2Authentication()


@dataclass
class LakehouseClient:
    """A connection to the lakehouse, scoped to one catalog."""

    catalog: str
    schema: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    user: str = field(default_factory=lambda: os.environ.get("TRINO_USER", getpass.getuser()))
    _conn: Any = field(default=None, init=False, repr=False)

    def connect(self) -> LakehouseClient:
        self._conn = trino.dbapi.connect(
            host=self.host,
            port=self.port,
            http_scheme="https",
            catalog=self.catalog,
            schema=self.schema,
            user=self.user,
            auth=_auth(),
        )
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> LakehouseClient:
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        """Run Trino SQL and return rows as dicts."""
        cur = self._conn.cursor()
        cur.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def passthrough(self, native_sql: str) -> list[dict]:
        """Run SQL natively on the backing database via the connector's query function.

        The native SQL is embedded inside a Trino string literal, so single quotes are
        doubled here. Callers write native SQL normally and never escape it themselves.
        """
        return self.query(wrap_passthrough(self.catalog, native_sql))


def wrap_passthrough(catalog: str, native_sql: str) -> str:
    """Build the ``system.query`` invocation for a catalog. Kept pure for testing."""
    escaped = native_sql.replace("'", "''")
    return f"SELECT * FROM TABLE({quote_catalog(catalog)}.system.query(query => '{escaped}'))"


def quote_catalog(catalog: str) -> str:
    """Catalog names such as ``gold-db-2_postgresql`` contain hyphens and must be quoted."""
    return '"' + catalog.replace('"', '""') + '"'


def list_catalogs(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[dict]:
    """List every catalog with its connector name.

    ``connector_name`` is how we pick a dialect. It is authoritative, unlike guessing
    from a catalog's name suffix, which is only a local naming convention.
    """
    client = LakehouseClient(catalog="system", host=host, port=port).connect()
    try:
        return client.query(
            "SELECT catalog_name, connector_name FROM system.metadata.catalogs "
            "ORDER BY catalog_name"
        )
    finally:
        client.close()
