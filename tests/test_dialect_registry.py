"""Dialect resolution: connector name wins, catalog suffix is the fallback."""

import pytest

from lakehouse_schema_extraction.dialects import (
    UnsupportedDialectError,
    get_dialect,
    registered_dialects,
)


def test_connector_name_selects_dialect():
    assert get_dialect("postgresql", "anything").name == "postgresql"


@pytest.mark.parametrize("catalog", ["gold-db-2_postgresql", "foo_postgres", "bar_pg"])
def test_suffix_fallback_when_connector_unknown(catalog):
    assert get_dialect(None, catalog).name == "postgresql"


def test_connector_name_beats_a_misleading_suffix():
    """A catalog named *_mysql but served by the postgresql connector is postgres."""
    assert get_dialect("postgresql", "legacy_mysql").name == "postgresql"


def test_unsupported_connector_raises_with_guidance():
    with pytest.raises(UnsupportedDialectError, match="supported"):
        get_dialect("iceberg", "lake_iceberg")


def test_unregistered_dialect_suffix_still_raises():
    """The mysql suffix hint exists, but no mysql dialect is registered yet."""
    with pytest.raises(UnsupportedDialectError):
        get_dialect(None, "warehouse_mysql")


def test_postgres_is_registered():
    assert "postgresql" in registered_dialects()
