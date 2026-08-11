"""Dialect registry.

Dialects are selected by Trino ``connector_name`` (``postgresql``, ``mysql``, ...),
which is reported by ``system.metadata.catalogs``. Catalog naming conventions such as
the ``_postgresql`` suffix used in this lakehouse are only a fallback, since a name is
a local convention while the connector name is what Trino actually loaded.

To add a database: implement :class:`~lakehouse_schema_extraction.dialects.base.Dialect`
in a new module and append it to ``_DIALECTS``. Nothing else changes.
"""

from __future__ import annotations

from lakehouse_schema_extraction.dialects.base import Dialect, SchemaMetadata
from lakehouse_schema_extraction.dialects.postgres import PostgresDialect

_DIALECTS: list[Dialect] = [
    PostgresDialect(),
]

# Fallback only: catalog-name suffix -> dialect name.
_SUFFIX_HINTS = {
    "_postgresql": "postgresql",
    "_postgres": "postgresql",
    "_pg": "postgresql",
    "_mysql": "mysql",
    "_mariadb": "mysql",
}


class UnsupportedDialectError(RuntimeError):
    """Raised when no dialect handles a catalog's connector."""


def registered_dialects() -> list[str]:
    return [d.name for d in _DIALECTS]


def get_dialect(connector_name: str | None = None, catalog: str | None = None) -> Dialect:
    """Resolve a dialect from a connector name, falling back to the catalog-name suffix."""
    if connector_name:
        for dialect in _DIALECTS:
            if connector_name in dialect.connector_names:
                return dialect

    if catalog:
        lowered = catalog.lower()
        for suffix, name in _SUFFIX_HINTS.items():
            if lowered.endswith(suffix):
                for dialect in _DIALECTS:
                    if dialect.name == name:
                        return dialect

    known = ", ".join(registered_dialects())
    raise UnsupportedDialectError(
        f"no dialect for connector {connector_name!r} (catalog {catalog!r}); "
        f"supported: {known}. Add one in lakehouse_schema_extraction/dialects/."
    )


__all__ = [
    "Dialect",
    "SchemaMetadata",
    "PostgresDialect",
    "UnsupportedDialectError",
    "get_dialect",
    "registered_dialects",
]
