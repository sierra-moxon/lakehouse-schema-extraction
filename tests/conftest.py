"""Fixtures modelling a small relational schema with a reference cycle.

Everything here is offline: the dialects are pure functions over metadata dicts, so
extraction and rendering are testable without a lakehouse connection.
"""

import pytest

from lakehouse_schema_extraction.dialects.base import SchemaMetadata


class FakeClient:
    """Records the native SQL it is handed and replays canned results."""

    def __init__(self, results=None, catalog="test-db_postgresql"):
        self.catalog = catalog
        self.results = results or {}
        self.seen: list[str] = []

    def passthrough(self, native_sql: str):
        self.seen.append(native_sql)
        for marker, rows in self.results.items():
            if marker in native_sql:
                return rows
        return []


@pytest.fixture
def meta() -> SchemaMetadata:
    return SchemaMetadata(
        catalog="gold-db-2_postgresql",
        schema="gold",
        dialect="postgresql",
        tables=[
            {"table_name": "study", "relkind": "r", "comment": "A GOLD study",
             "approx_rows": 100},
            {"table_name": "biosample", "relkind": "r", "comment": None,
             "approx_rows": 5000},
            {"table_name": "study_summary", "relkind": "v", "comment": None,
             "approx_rows": 0},
            {"table_name": "study_rollup", "relkind": "m", "comment": None,
             "approx_rows": 0},
        ],
        columns=[
            {"table_name": "study", "ordinal_position": 1, "column_name": "study_id",
             "data_type": "integer", "not_null": True,
             "default_expr": "nextval('gold.study_seq'::regclass)", "comment": "PK"},
            {"table_name": "study", "ordinal_position": 2, "column_name": "name",
             "data_type": "character varying(255)", "not_null": False,
             "default_expr": None, "comment": "It's a name"},
            {"table_name": "study", "ordinal_position": 3, "column_name": "lead_biosample_id",
             "data_type": "integer", "not_null": False, "default_expr": None,
             "comment": None},
            {"table_name": "biosample", "ordinal_position": 1, "column_name": "biosample_id",
             "data_type": "integer", "not_null": True, "default_expr": None, "comment": None},
            {"table_name": "biosample", "ordinal_position": 2, "column_name": "study_id",
             "data_type": "integer", "not_null": False, "default_expr": None, "comment": None},
        ],
        constraints=[
            {"table_name": "study", "constraint_name": "study_pkey",
             "constraint_type": "PRIMARY KEY", "definition": "PRIMARY KEY (study_id)",
             "target_table": None, "columns": ["study_id"], "target_columns": []},
            {"table_name": "biosample", "constraint_name": "biosample_pkey",
             "constraint_type": "PRIMARY KEY", "definition": "PRIMARY KEY (biosample_id)",
             "target_table": None, "columns": ["biosample_id"], "target_columns": []},
            # Deliberate cycle: study -> biosample -> study
            {"table_name": "biosample", "constraint_name": "biosample_study_fk",
             "constraint_type": "FOREIGN KEY",
             "definition": "FOREIGN KEY (study_id) REFERENCES gold.study(study_id)",
             "target_table": "study", "columns": ["study_id"],
             "target_columns": ["study_id"]},
            {"table_name": "study", "constraint_name": "study_lead_biosample_fk",
             "constraint_type": "FOREIGN KEY",
             "definition": "FOREIGN KEY (lead_biosample_id) "
                           "REFERENCES gold.biosample(biosample_id)",
             "target_table": "biosample", "columns": ["lead_biosample_id"],
             "target_columns": ["biosample_id"]},
            {"table_name": "study", "constraint_name": "study_name_check",
             "constraint_type": "CHECK", "definition": "CHECK ((name <> ''::text))",
             "target_table": None, "columns": ["name"], "target_columns": []},
        ],
        indexes=[
            {"table_name": "study", "index_name": "study_pkey",
             "definition": "CREATE UNIQUE INDEX study_pkey ON gold.study USING btree (study_id)"},
            {"table_name": "biosample", "index_name": "biosample_study_idx",
             "definition": "CREATE INDEX biosample_study_idx ON gold.biosample "
                           "USING btree (study_id)"},
            # pg_indexes reports matview indexes alongside table indexes
            {"table_name": "study_rollup", "index_name": "study_rollup_idx",
             "definition": "CREATE INDEX study_rollup_idx ON gold.study_rollup "
                           "USING btree (count)"},
        ],
        views=[
            {"view_name": "study_summary", "view_kind": "view",
             "definition": " SELECT study.study_id, study.name FROM gold.study;"},
            {"view_name": "study_rollup", "view_kind": "materialized",
             "definition": " SELECT count(*) FROM gold.study_summary;"},
        ],
        sequences=[
            {"sequence_name": "study_seq", "data_type": "bigint", "start_value": 1,
             "increment_by": 1},
        ],
        extensions=[
            {"extension_name": "pg_trgm", "schema_name": "public"},
        ],
    )
