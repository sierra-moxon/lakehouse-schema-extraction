"""Refinement of schema-automator output.

The invariant that matters most: foreign-key object ranges are what schema-automator
gets right and we must never clobber, while scalar types are what it loses and we must
restore.
"""

import pytest

from lakehouse_schema_extraction.linkml_refine import normalise_type, refine


@pytest.fixture
def automator_output():
    """Shaped like real `schemauto import-sql` output: FKs resolved, types flattened."""
    return {
        "name": "test-schema",  # -n is ignored upstream, so this is always wrong
        "id": "http://example.org/test-schema",
        "classes": {
            "study": {
                "attributes": {
                    "study_id": {"identifier": True, "range": "string"},
                    "name": {"range": "string"},
                    "created": {"range": "string"},
                    "score": {"range": "string"},
                    "active": {"range": "string"},
                    "tags": {"range": "string"},
                }
            },
            "biosample": {
                "attributes": {
                    "biosample_id": {"identifier": True, "range": "string"},
                    "study_id": {"range": "study"},  # resolved foreign key
                }
            },
        },
    }


@pytest.fixture
def metadata():
    def col(table, name, dtype, not_null=False, comment=None):
        return {"table_name": table, "column_name": name, "data_type": dtype,
                "not_null": not_null, "comment": comment}

    return {
        "catalog": "gold-db-2_postgresql",
        "schema": "gold",
        "tables": [{"table_name": "study", "comment": None},
                   {"table_name": "biosample", "comment": None}],
        "columns": [
            col("study", "study_id", "integer", not_null=True),
            col("study", "name", "character varying(255)", not_null=True,
                comment="Study name"),
            col("study", "created", "timestamp without time zone"),
            col("study", "score", "numeric(10,2)"),
            col("study", "active", "boolean"),
            col("study", "tags", "text[]"),
            col("biosample", "biosample_id", "integer", not_null=True),
            col("biosample", "study_id", "integer"),
        ],
    }


@pytest.fixture
def refined(automator_output, metadata):
    """Default path: names kept as in the database, for direct column lookups."""
    schema, _ = refine(automator_output, metadata, name="gold", camel_case=False)
    return schema


@pytest.fixture
def camel(automator_output, metadata):
    schema, _ = refine(automator_output, metadata, name="gold", camel_case=True)
    return schema


def attr(schema, cls, name):
    return schema["classes"][cls]["attributes"][name]


class TestForeignKeysArePreserved:
    def test_object_range_survives_refinement(self, refined):
        """biosample.study_id points at the study class; it must not become integer."""
        assert attr(refined, "biosample", "study_id")["range"] == "study"

    def test_object_range_is_not_marked_required_from_the_column(self, refined):
        """FK slots are skipped wholesale, so no nullability is applied to them."""
        assert "required" not in attr(refined, "biosample", "study_id")


class TestCamelCaseRenaming:
    def test_classes_are_renamed(self, camel):
        assert set(camel["classes"]) == {"Study", "Biosample"}

    def test_foreign_key_ranges_follow_the_rename(self, camel):
        """A range still naming the old class would dangle."""
        assert attr(camel, "Biosample", "study_id")["range"] == "Study"

    def test_no_range_points_at_a_missing_class(self, camel):
        names = set(camel["classes"])
        for cls in camel["classes"].values():
            for a in cls["attributes"].values():
                rng = a["range"]
                assert rng in names or rng in {"string", "integer", "decimal", "datetime",
                                               "boolean", "date", "float", "double",
                                               "time", "uriorcurie"}

    def test_source_table_is_kept_as_an_alias(self, camel):
        assert camel["classes"]["Study"]["aliases"] == ["study"]

    def test_types_are_still_refined_under_renaming(self, camel):
        """Column lookup uses the original table name, not the renamed class."""
        assert attr(camel, "Study", "created")["range"] == "datetime"
        assert attr(camel, "Study", "name")["required"] is True

    def test_collision_falls_back_to_database_names(self, automator_output, metadata):
        """study_x and studyX both become StudyX, which would silently merge two tables."""
        automator_output["classes"]["study_x"] = {"attributes": {}}
        automator_output["classes"]["studyX"] = {"attributes": {}}
        schema, report = refine(automator_output, metadata, name="gold", camel_case=True)
        assert {"study_x", "studyX", "study"} <= set(schema["classes"])
        assert any("collide" in line for line in report)


class TestTypesAreRestored:
    @pytest.mark.parametrize("column,expected", [
        ("study_id", "integer"),
        ("name", "string"),
        ("created", "datetime"),
        ("score", "decimal"),
        ("active", "boolean"),
    ])
    def test_scalar_ranges_come_from_native_types(self, refined, column, expected):
        assert attr(refined, "study", column)["range"] == expected

    def test_array_column_becomes_multivalued(self, refined):
        assert attr(refined, "study", "tags")["multivalued"] is True


class TestNullabilityAndDescriptions:
    def test_not_null_becomes_required(self, refined):
        assert attr(refined, "study", "name")["required"] is True

    def test_nullable_column_is_not_required(self, refined):
        assert "required" not in attr(refined, "study", "created")

    def test_identifier_is_not_also_marked_required(self, refined):
        """An identifier is required by definition; restating it is noise."""
        assert "required" not in attr(refined, "study", "study_id")

    def test_column_comment_becomes_description(self, refined):
        assert attr(refined, "study", "name")["description"] == "Study name"


class TestSchemaHeader:
    def test_name_overrides_the_hardcoded_upstream_default(self, refined):
        assert refined["name"] == "gold"
        assert "test-schema" not in refined["id"]

    def test_types_are_imported(self, refined):
        assert "linkml:types" in refined["imports"]

    def test_prefixes_include_linkml_and_the_schema(self, refined):
        assert "linkml" in refined["prefixes"]
        assert "gold" in refined["prefixes"]

    def test_title_records_provenance(self, refined):
        assert refined["title"] == "gold-db-2_postgresql.gold"


class TestReporting:
    def test_report_counts_preserved_links_and_retypes(self, automator_output, metadata):
        _, report = refine(automator_output, metadata, name="gold", camel_case=False)
        joined = " ".join(report)
        assert "1 foreign-key object ranges preserved" in joined
        assert "2 classes" in joined

    def test_unmatched_slots_are_reported_not_dropped(self, automator_output, metadata):
        automator_output["classes"]["study"]["attributes"]["ghost"] = {"range": "string"}
        schema, report = refine(automator_output, metadata, name="gold", camel_case=False)
        assert "ghost" in schema["classes"]["study"]["attributes"]
        assert any("no matching column" in line for line in report)


class TestInputIsNotMutated:
    def test_original_schema_is_left_alone(self, automator_output, metadata):
        refine(automator_output, metadata, name="gold")
        assert automator_output["name"] == "test-schema"
        assert automator_output["classes"]["study"]["attributes"]["created"]["range"] == "string"


class TestTypeNormalisation:
    @pytest.mark.parametrize("pg,expected", [
        ("character varying(255)", "string"),
        ("numeric(10,2)", "decimal"),
        ("timestamp with time zone", "datetime"),
        ("double precision", "double"),
        ("BIGINT", "integer"),
        ("some_custom_domain", "string"),
    ])
    def test_precision_and_case_are_stripped(self, pg, expected):
        assert normalise_type(pg)[0] == expected

    def test_array_marker_is_detected_and_stripped(self):
        assert normalise_type("integer[]") == ("integer", True)
