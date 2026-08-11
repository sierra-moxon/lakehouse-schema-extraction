"""Discovery of every (catalog, schema) pair worth extracting.

Skips are deliberate and always reported, never silent: a schema that is dropped from a
sweep without explanation looks identical to one that was never there. Every skip
carries a reason, and ``--all`` disables the rules entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lakehouse_schema_extraction.client import LakehouseClient, list_catalogs
from lakehouse_schema_extraction.dialects import UnsupportedDialectError, get_dialect

# Catalogs that are infrastructure rather than data worth documenting.
SKIP_CATALOGS = {
    "starburst-db_postgresql",  # Starburst's own metadata store
}

# Oracle compatibility shims installed by orafce / oracle_fdw migrations. These carry
# stub functions, not domain tables, and appear in databases migrated from Oracle.
SKIP_SCHEMA_NAMES = {
    "oracle",
    "utl_file",
    "plunit",
    "pgagent",
    "pgtt_schema",
    "pgtle",
}
SKIP_SCHEMA_PREFIXES = ("dbms_", "plv", "utl_", "_timescaledb")


def skip_reason(catalog: str, schema: str, table_count: int) -> str | None:
    """Return why this pair should be skipped, or None to process it."""
    if catalog in SKIP_CATALOGS:
        return "infrastructure catalog"
    if schema in SKIP_SCHEMA_NAMES:
        return "oracle compatibility shim"
    if schema.startswith(SKIP_SCHEMA_PREFIXES):
        return "oracle compatibility shim"
    if table_count == 0:
        return "no tables"
    return None


@dataclass
class Target:
    catalog: str
    schema: str
    connector: str
    table_count: int = 0
    view_count: int = 0
    skipped: str | None = None

    @property
    def slug(self) -> str:
        """Filesystem- and database-safe name, unique across catalogs."""
        return sanitise(self.catalog)

    def to_dict(self) -> dict:
        return {
            "catalog": self.catalog,
            "schema": self.schema,
            "connector": self.connector,
            "table_count": self.table_count,
            "view_count": self.view_count,
            "skipped": self.skipped,
        }


@dataclass
class Discovery:
    targets: list[Target] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def selected(self) -> list[Target]:
        return [t for t in self.targets if not t.skipped]

    @property
    def skipped(self) -> list[Target]:
        return [t for t in self.targets if t.skipped]


def sanitise(name: str) -> str:
    """gold-db-2_postgresql -> gold_db_2_postgresql (legal as an identifier or path)."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def discover(host: str, port: int, include_all: bool = False) -> Discovery:
    """Enumerate every schema of every catalog a dialect supports.

    One connection and one catalog-wide count query per catalog; nothing is extracted
    here. A catalog that cannot be reached is recorded as an error rather than aborting
    the sweep, since one broken connector should not hide the other five.
    """
    result = Discovery()

    for row in list_catalogs(host=host, port=port):
        catalog, connector = row["catalog_name"], row["connector_name"]
        try:
            dialect = get_dialect(connector, catalog)
        except UnsupportedDialectError:
            continue  # not an error: most catalogs are simply another engine

        if catalog in SKIP_CATALOGS and not include_all:
            result.targets.append(
                Target(catalog, "*", connector, skipped="infrastructure catalog")
            )
            continue

        try:
            with LakehouseClient(catalog=catalog, host=host, port=port) as client:
                counts = dialect.schema_table_counts(client)
        except Exception as exc:  # noqa: BLE001 - one bad catalog must not stop the rest
            result.errors.append({"catalog": catalog, "error": str(exc)})
            continue

        for entry in counts:
            schema = entry["schema_name"]
            tables = int(entry.get("table_count") or 0)
            views = int(entry.get("view_count") or 0)
            reason = None if include_all else skip_reason(catalog, schema, tables)
            result.targets.append(
                Target(catalog, schema, connector, tables, views, reason)
            )

    return result
