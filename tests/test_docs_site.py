"""Site index and navigation generation.

The load-bearing invariant: identically-named schemas in different catalogs must never
share a documentation path. `public` exists in four of the five JGI catalogs, so a
path keyed on schema name alone would have them overwrite each other.
"""

import pytest
import yaml

from lakehouse_schema_extraction.docs_site import (
    catalog_label,
    collect,
    render_index,
    render_mkdocs_config,
    summarise,
)


def write_schema(root, catalog, schema, classes):
    d = root / catalog
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{schema}.linkml.yaml").write_text(
        yaml.safe_dump({"name": schema, "title": f"{catalog}.{schema}",
                        "classes": classes})
    )


@pytest.fixture
def schema_dir(tmp_path):
    write_schema(tmp_path, "gold-db-2_postgresql", "gold", {
        "Study": {"attributes": {
            "study_id": {"range": "integer", "identifier": True},
            "name": {"range": "string", "required": True},
        }},
        "Biosample": {"attributes": {
            "biosample_id": {"range": "integer", "identifier": True},
            "study_id": {"range": "Study"},
        }},
    })
    # Same schema name, different catalogs -- the collision case.
    write_schema(tmp_path, "smc-db_postgresql", "public", {
        "Bgc": {"attributes": {"bgc_id": {"range": "integer"}}},
    })
    write_schema(tmp_path, "img-db-2_postgresql", "public", {
        "Taxon": {"attributes": {"taxon_oid": {"range": "integer"}}},
    })
    return tmp_path


class TestCollect:
    def test_finds_every_schema(self, schema_dir):
        assert len(collect(schema_dir)) == 3

    def test_doc_paths_are_unique_across_catalogs(self, schema_dir):
        """Two `public` schemas must not resolve to the same docs directory."""
        paths = [r["doc_path"] for r in collect(schema_dir)]
        assert len(paths) == len(set(paths))

    def test_doc_path_includes_the_catalog(self, schema_dir):
        row = next(r for r in collect(schema_dir) if r["catalog"] == "smc-db_postgresql")
        assert row["doc_path"] == "smc-db_postgresql/public/index.md"

    def test_schema_name_is_stripped_of_the_linkml_suffix(self, schema_dir):
        assert all(not r["schema"].endswith(".linkml") for r in collect(schema_dir))


class TestSummarise:
    def test_counts_classes_slots_and_object_references(self, schema_dir):
        s = summarise(schema_dir / "gold-db-2_postgresql" / "gold.linkml.yaml")
        assert s["classes"] == 2
        assert s["slots"] == 4
        assert s["links"] == 1       # Biosample.study_id -> Study
        assert s["required"] == 1

    def test_schema_with_no_classes_does_not_crash(self, tmp_path):
        (tmp_path / "c").mkdir()
        (tmp_path / "c" / "empty.linkml.yaml").write_text("name: empty\n")
        assert summarise(tmp_path / "c" / "empty.linkml.yaml")["classes"] == 0


class TestIndexPage:
    def test_each_catalog_gets_a_section(self, schema_dir):
        md = render_index(collect(schema_dir))
        assert "## GOLD" in md
        assert "## SMC" in md
        assert "## IMG" in md

    def test_links_point_at_per_catalog_paths(self, schema_dir):
        md = render_index(collect(schema_dir))
        assert "(smc-db_postgresql/public/index.md)" in md
        assert "(img-db-2_postgresql/public/index.md)" in md

    def test_schemas_without_references_are_called_out(self, schema_dir):
        md = render_index(collect(schema_dir))
        assert "No declared relationships" in md

    def test_totals_are_reported(self, schema_dir):
        md = render_index(collect(schema_dir))
        assert "**3 schemas**" in md


class TestMkdocsConfig:
    @pytest.fixture
    def config(self, schema_dir):
        return yaml.safe_load(render_mkdocs_config(collect(schema_dir), "https://x/y"))

    def test_nav_starts_with_home(self, config):
        assert config["nav"][0] == {"Home": "index.md"}

    def test_every_schema_appears_in_the_nav(self, config, schema_dir):
        flat = yaml.safe_dump(config["nav"])
        for row in collect(schema_dir):
            assert row["doc_path"] in flat

    def test_catalogs_become_nav_sections(self, config):
        labels = [next(iter(entry)) for entry in config["nav"][1:]]
        assert labels == ["GOLD", "IMG", "SMC"]

    def test_generated_header_warns_against_hand_editing(self, schema_dir):
        raw = render_mkdocs_config(collect(schema_dir), "https://x/y")
        assert raw.startswith("# GENERATED FILE")

    def test_repo_url_is_applied(self, config):
        assert config["repo_url"] == "https://x/y"


class TestCatalogLabels:
    def test_known_catalogs_get_friendly_names(self):
        assert catalog_label("gold-db-2_postgresql") == "GOLD"

    def test_unknown_catalog_falls_back_to_a_tidied_name(self):
        """A newly federated database must appear without a code change."""
        assert catalog_label("new-thing-1_postgresql") == "New Thing 1"
