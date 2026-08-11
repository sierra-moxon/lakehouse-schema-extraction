"""Pass-through construction: quoting is the part that breaks in practice."""

from lakehouse_schema_extraction.client import quote_catalog, wrap_passthrough


def test_hyphenated_catalog_is_quoted():
    assert quote_catalog("gold-db-2_postgresql") == '"gold-db-2_postgresql"'


def test_embedded_quotes_in_catalog_are_doubled():
    assert quote_catalog('we"ird') == '"we""ird"'


def test_native_sql_single_quotes_are_doubled():
    """A literal like 'gold' must survive nesting inside a Trino string literal."""
    sql = wrap_passthrough("c_postgresql", "SELECT 1 WHERE nspname = 'gold'")
    assert "''gold''" in sql
    assert sql.startswith('SELECT * FROM TABLE("c_postgresql".system.query(query => \'')
    assert sql.endswith("'))")


def test_passthrough_has_no_unbalanced_quotes():
    """Doubling must leave an even number of quotes inside the outer literal."""
    inner = "SELECT * FROM t WHERE a = 'x' AND b = 'y'"
    body = wrap_passthrough("c", inner).split("query => ", 1)[1]
    assert body.count("'") % 2 == 0
