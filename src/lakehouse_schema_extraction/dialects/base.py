"""The contract every database dialect implements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass
class SchemaMetadata:
    """Raw per-relation metadata pulled from a database's native catalog.

    Field names are deliberately generic so downstream consumers (LinkML generation,
    diffing, documentation) work the same regardless of source database.
    """

    catalog: str
    schema: str
    dialect: str
    tables: list[dict] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    views: list[dict] = field(default_factory=list)
    sequences: list[dict] = field(default_factory=list)
    extensions: list[dict] = field(default_factory=list)

    @property
    def foreign_keys(self) -> list[dict]:
        return [c for c in self.constraints if c.get("constraint_type") == "FOREIGN KEY"]

    @property
    def primary_keys(self) -> list[dict]:
        return [c for c in self.constraints if c.get("constraint_type") == "PRIMARY KEY"]

    def to_dict(self) -> dict:
        return {
            "catalog": self.catalog,
            "schema": self.schema,
            "dialect": self.dialect,
            "tables": self.tables,
            "columns": self.columns,
            "constraints": self.constraints,
            "indexes": self.indexes,
            "views": self.views,
            "sequences": self.sequences,
            "extensions": self.extensions,
        }


@runtime_checkable
class Dialect(Protocol):
    """A database-specific metadata extractor.

    Implementations do two things: pull native catalog metadata into a
    :class:`SchemaMetadata`, and render that back out as loadable DDL.
    """

    name: str
    connector_names: tuple[str, ...]

    def extract(self, client, schema: str) -> SchemaMetadata:
        """Query the backing database's catalog via ``client.passthrough``."""
        ...

    def render_ddl(self, meta: SchemaMetadata) -> str:
        """Render metadata as DDL that loads into an empty database of this type."""
        ...

    def list_schemas(self, client) -> list[str]:
        """List user schemas in the catalog, excluding system ones."""
        ...


def quote_ident(name: str, quote_char: str = '"') -> str:
    """Quote an identifier only when the target database would require it."""
    if SAFE_IDENT.match(name):
        return name
    return quote_char + name.replace(quote_char, quote_char * 2) + quote_char


def quote_literal(value: str) -> str:
    """Escape a string for use in a SQL literal."""
    return value.replace("'", "''")
