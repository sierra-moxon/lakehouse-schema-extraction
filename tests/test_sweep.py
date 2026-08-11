"""Discovery skip rules.

Skips must be explainable. Every rule here corresponds to something actually present
in the JGI lakehouse, so the reasons double as documentation of what was excluded.
"""

import pytest

from lakehouse_schema_extraction.sweep import Target, sanitise, skip_reason


class TestSkipRules:
    @pytest.mark.parametrize("schema", [
        "dbms_output", "dbms_random", "plvstr", "plvdate", "plunit", "utl_file", "oracle",
    ])
    def test_oracle_compatibility_shims_are_skipped(self, schema):
        assert skip_reason("img-db-2_postgresql", schema, 5) == "oracle compatibility shim"

    def test_infrastructure_catalog_is_skipped(self):
        assert skip_reason("starburst-db_postgresql", "public", 10) == "infrastructure catalog"

    def test_empty_schema_is_skipped(self):
        assert skip_reason("gold-db-2_postgresql", "scratch", 0) == "no tables"

    @pytest.mark.parametrize("catalog,schema", [
        ("gold-db-2_postgresql", "gold"),
        ("gold-db-2_postgresql", "public"),
        ("img-db-2_postgresql", "img_core_v400"),
        ("plant-db-7_postgresql", "denormalized"),
    ])
    def test_real_schemas_are_kept(self, catalog, schema):
        assert skip_reason(catalog, schema, 12) is None

    def test_a_schema_named_like_a_shim_but_with_tables_is_still_skipped(self):
        """The prefix rule is intentional: these carry stub functions, not domain data."""
        assert skip_reason("img-db-2_postgresql", "dbms_sql", 99) is not None

    def test_plv_prefix_does_not_swallow_unrelated_names(self):
        assert skip_reason("c_postgresql", "plants", 3) is None


class TestSanitise:
    @pytest.mark.parametrize("raw,expected", [
        ("gold-db-2_postgresql", "gold_db_2_postgresql"),
        ("img-db-2_postgresql", "img_db_2_postgresql"),
        ("UPPER-Case", "upper_case"),
        ("trailing---", "trailing"),
    ])
    def test_names_become_legal_identifiers(self, raw, expected):
        assert sanitise(raw) == expected

    def test_distinct_catalogs_stay_distinct(self):
        """Collapsing two catalogs to one name would merge their databases."""
        names = {sanitise(c) for c in
                 ["gold-db-2_postgresql", "img-db-2_postgresql", "smc-db_postgresql"]}
        assert len(names) == 3


class TestTarget:
    def test_slug_is_derived_from_the_catalog(self):
        t = Target("gold-db-2_postgresql", "gold", "postgresql")
        assert t.slug == "gold_db_2_postgresql"

    def test_to_dict_round_trips_the_skip_reason(self):
        t = Target("c", "s", "postgresql", skipped="no tables")
        assert t.to_dict()["skipped"] == "no tables"
