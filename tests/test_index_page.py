"""The index page must reflect what is on disk, not what a run intended to produce."""

import json

import pytest

from lakehouse_schema_extraction.index_page import collect, render


def write_extraction(out_dir, catalog, schema, *, fks=1, linkml=False, erd=False):
    cat_dir = out_dir / catalog
    cat_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "catalog": catalog,
        "schema": schema,
        "tables": [{"table_name": "t1", "relkind": "r"},
                   {"table_name": "v1", "relkind": "v"}],
        "columns": [{"table_name": "t1", "column_name": "id"}],
        "constraints": ([{"constraint_type": "FOREIGN KEY"}] * fks)
                       + [{"constraint_type": "PRIMARY KEY"}],
        "views": [{"view_name": "v1"}],
    }
    (cat_dir / f"{schema}.json").write_text(json.dumps(meta))
    (cat_dir / f"{schema}.sql").write_text("-- ddl")
    if linkml:
        (cat_dir / f"{schema}.linkml.yaml").write_text("name: x")
    if erd:
        spy = out_dir / "schemaspy" / catalog / schema
        spy.mkdir(parents=True, exist_ok=True)
        (spy / "index.html").write_text("<html></html>")


@pytest.fixture
def out_dir(tmp_path):
    write_extraction(tmp_path, "gold-db-2_postgresql", "gold", fks=3, linkml=True, erd=True)
    write_extraction(tmp_path, "gold-db-2_postgresql", "public", fks=0)
    write_extraction(tmp_path, "smc-db_postgresql", "public", fks=2, linkml=True)
    return tmp_path


class TestCollect:
    def test_finds_every_extraction(self, out_dir):
        assert len(collect(out_dir)) == 3

    def test_counts_only_real_tables_not_views(self, out_dir):
        row = next(r for r in collect(out_dir) if r["schema"] == "gold")
        assert row["tables"] == 1

    def test_reports_missing_artifacts_as_absent(self, out_dir):
        row = next(r for r in collect(out_dir)
                   if r["catalog"] == "smc-db_postgresql")
        assert row["linkml"] is not None
        assert row["schemaspy"] is None

    def test_identically_named_schemas_in_different_catalogs_stay_separate(self, out_dir):
        publics = [r for r in collect(out_dir) if r["schema"] == "public"]
        assert len(publics) == 2
        assert {r["catalog"] for r in publics} == {
            "gold-db-2_postgresql", "smc-db_postgresql"
        }

    def test_unreadable_json_is_skipped_not_fatal(self, out_dir):
        (out_dir / "broken_postgresql").mkdir()
        (out_dir / "broken_postgresql" / "s.json").write_text("{not json")
        assert len(collect(out_dir)) == 3


class TestRender:
    def test_links_are_relative_to_the_output_directory(self, out_dir):
        html = render(collect(out_dir), out_dir)
        assert 'href="schemaspy/gold-db-2_postgresql/gold/index.html"' in html
        assert str(out_dir) not in html

    def test_missing_artifacts_render_as_plain_text_not_links(self, out_dir):
        html = render(collect(out_dir), out_dir)
        assert '<span class="missing">ERD</span>' in html

    def test_zero_foreign_keys_is_flagged(self, out_dir):
        html = render(collect(out_dir), out_dir)
        assert "warn" in html

    def test_catalogs_get_their_own_section(self, out_dir):
        html = render(collect(out_dir), out_dir)
        assert html.count("<caption>") == 2

    def test_totals_are_summed_across_catalogs(self, out_dir):
        html = render(collect(out_dir), out_dir)
        assert "3 schemas" in html
        assert "5 foreign keys" in html
