"""Rendering of Postgres DDL from extracted metadata."""

import pytest

from lakehouse_schema_extraction.dialects.base import quote_ident
from lakehouse_schema_extraction.dialects.postgres import PostgresDialect

from .conftest import FakeClient


@pytest.fixture
def ddl(meta) -> str:
    return PostgresDialect().render_ddl(meta)


class TestOrdering:
    def test_sequences_precede_tables(self, ddl):
        """serial defaults reference a sequence, so it must already exist."""
        assert ddl.index("CREATE SEQUENCE") < ddl.index("CREATE TABLE")

    def test_foreign_keys_come_after_every_table(self, ddl):
        """The fixture has a study <-> biosample cycle; inline FKs could not load."""
        assert ddl.rindex("CREATE TABLE") < ddl.index("ALTER TABLE")

    def test_cyclic_foreign_keys_are_both_emitted_as_alters(self, ddl):
        assert "ADD CONSTRAINT biosample_study_fk" in ddl
        assert "ADD CONSTRAINT study_lead_biosample_fk" in ddl
        assert "FOREIGN KEY" not in ddl.split("-- foreign keys")[0]


class TestColumns:
    def test_types_defaults_and_nullability(self, ddl):
        assert "name character varying(255)" in ddl
        assert "study_id integer DEFAULT nextval('gold.study_seq'::regclass) NOT NULL" in ddl

    def test_nullable_column_has_no_not_null(self, ddl):
        line = next(line for line in ddl.splitlines() if "lead_biosample_id integer" in line)
        assert "NOT NULL" not in line


class TestConstraints:
    def test_primary_key_is_inline(self, ddl):
        assert "CONSTRAINT study_pkey PRIMARY KEY (study_id)" in ddl

    def test_check_constraint_is_inline(self, ddl):
        assert "CONSTRAINT study_name_check CHECK" in ddl


class TestIndexes:
    def test_constraint_backing_index_is_not_reissued(self, ddl):
        """study_pkey is created by the PK; emitting it again would be a duplicate."""
        assert "CREATE UNIQUE INDEX study_pkey" not in ddl

    def test_ordinary_index_is_kept(self, ddl):
        assert "CREATE INDEX biosample_study_idx" in ddl

    def test_matview_index_is_emitted_after_the_matview(self, ddl):
        """pg_indexes lists matview indexes; creating one first fails with 'does not exist'."""
        assert ddl.index("CREATE MATERIALIZED VIEW") < ddl.index("CREATE INDEX study_rollup_idx")

    def test_table_indexes_still_precede_views(self, ddl):
        assert ddl.index("CREATE INDEX biosample_study_idx") < ddl.index("-- views")


class TestUserDefinedTypes:
    def test_enum_is_created_before_any_table(self, ddl):
        """A missing enum fails the CREATE TABLE and cascades to its FKs and indexes."""
        assert "CREATE TYPE gold.strand AS ENUM ('plus', 'minus');" in ddl
        assert ddl.index("CREATE TYPE") < ddl.index("CREATE TABLE")

    def test_domain_is_emitted_with_its_constraints(self, ddl):
        assert "CREATE DOMAIN gold.positive_int AS integer NOT NULL;" in ddl

    def test_types_come_after_extensions(self, ddl):
        assert ddl.index("CREATE EXTENSION") < ddl.index("CREATE TYPE")


class TestSearchPath:
    def test_search_path_is_set_for_unqualified_references(self, ddl):
        """pg_get_constraintdef emits `REFERENCES foo(id)` unqualified for tables on the
        source session's path; without this they do not resolve on load."""
        assert "SET search_path TO gold, public;" in ddl
        assert ddl.index("SET search_path") < ddl.index("CREATE TABLE")

    def test_search_path_follows_schema_creation(self, ddl):
        assert ddl.index("CREATE SCHEMA") < ddl.index("SET search_path")


class TestExtensions:
    def test_extensions_precede_everything_that_uses_them(self, ddl):
        """GOLD indexes use gin_trgm_ops, which requires pg_trgm to exist first."""
        assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in ddl
        assert ddl.index("CREATE EXTENSION") < ddl.index("CREATE TABLE")


class TestViewsAndComments:
    def test_view_is_emitted_and_not_created_as_a_table(self, ddl):
        assert "CREATE OR REPLACE VIEW gold.study_summary AS" in ddl
        assert "CREATE TABLE gold.study_summary" not in ddl

    def test_materialized_view_uses_its_own_syntax(self, ddl):
        """There is no CREATE OR REPLACE MATERIALIZED VIEW in Postgres."""
        assert "CREATE MATERIALIZED VIEW gold.study_rollup AS" in ddl
        assert "OR REPLACE MATERIALIZED" not in ddl

    def test_comment_apostrophe_is_escaped(self, ddl):
        """A comment containing an apostrophe must not terminate the SQL literal."""
        assert "IS 'It''s a name'" in ddl

    def test_null_comments_are_skipped(self, ddl):
        assert "COMMENT ON TABLE gold.biosample" not in ddl


class TestMetadataHelpers:
    def test_foreign_keys_property(self, meta):
        assert {c["constraint_name"] for c in meta.foreign_keys} == {
            "biosample_study_fk", "study_lead_biosample_fk"
        }

    def test_primary_keys_property(self, meta):
        assert len(meta.primary_keys) == 2

    def test_to_dict_is_json_serialisable(self, meta):
        import json
        assert json.loads(json.dumps(meta.to_dict(), default=str))["schema"] == "gold"


class TestExtraction:
    def test_constraints_are_normalised_to_portable_types(self):
        client = FakeClient(results={
            "pg_constraint": [
                {"table_name": "t", "constraint_name": "t_fk", "contype": "f",
                 "definition": "FOREIGN KEY (a) REFERENCES u(b)", "target_table": "u",
                 "column_list": "a", "target_column_list": "b"},
            ],
        })
        meta = PostgresDialect().extract(client, "gold")
        assert meta.constraints[0]["constraint_type"] == "FOREIGN KEY"
        assert meta.constraints[0]["columns"] == ["a"]
        assert meta.constraints[0]["target_columns"] == ["b"]

    def test_empty_column_list_becomes_empty_list_not_blank_string(self):
        client = FakeClient(results={
            "pg_constraint": [
                {"table_name": "t", "constraint_name": "t_pk", "contype": "p",
                 "definition": "PRIMARY KEY (a)", "target_table": None,
                 "column_list": "a", "target_column_list": None},
            ],
        })
        meta = PostgresDialect().extract(client, "gold")
        assert meta.constraints[0]["target_columns"] == []

    def test_schema_name_is_escaped_into_queries(self):
        """A schema name with an apostrophe must not break out of the literal."""
        client = FakeClient()
        PostgresDialect().extract(client, "o'brien")
        assert client.seen
        for sql in client.seen:
            # Every occurrence must be the doubled form; none may remain bare.
            assert "o'brien" not in sql.replace("o''brien", "")


class TestQueryTypeSafety:
    """Types the Trino connector cannot map fail the whole query, not just a column.

    Regression: pg_sequences.data_type is a regtype and raised
    'Unsupported type: JdbcTypeHandle[jdbcType=1111, jdbcTypeName=regtype]'.
    """

    def test_sequence_data_type_is_cast_to_text(self):
        from lakehouse_schema_extraction.dialects.postgres import Q_SEQUENCES
        assert "data_type::text" in Q_SEQUENCES

    def test_reltuples_is_cast(self):
        from lakehouse_schema_extraction.dialects.postgres import Q_TABLES
        assert "reltuples::bigint" in Q_TABLES


class TestQuoting:
    @pytest.mark.parametrize("name", ["study", "study_id", "_x9"])
    def test_plain_identifiers_are_left_bare(self, name):
        assert quote_ident(name) == name

    @pytest.mark.parametrize("name", ["Study", "my table", "select-me"])
    def test_irregular_identifiers_are_quoted(self, name):
        assert quote_ident(name) == f'"{name}"'

    def test_embedded_double_quote_is_doubled(self):
        assert quote_ident('a"b') == '"a""b"'
