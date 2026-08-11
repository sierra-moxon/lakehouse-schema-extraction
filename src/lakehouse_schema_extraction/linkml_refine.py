"""Refine a schema-automator LinkML schema using precise extracted metadata.

schema-automator's ``import-sql`` gets the hard part right: it resolves foreign keys
into object-valued ranges and marks primary keys as identifiers. What it loses is type
fidelity -- SQLAlchemy reflection collapses dates, timestamps, numerics, and booleans
into ``string`` -- along with nullability and any schema naming passed on the command
line (``-n`` is ignored upstream).

We already hold exact ``format_type`` output for every column in the extracted JSON, so
this pass restores what was lost without touching what was gained. Object ranges are
never overwritten: an attribute whose range names a class is a resolved foreign key.
"""

from __future__ import annotations

import re
from typing import Any

# Postgres type (normalised, without length/precision) -> LinkML builtin range.
TYPE_MAP = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "serial": "integer",
    "bigserial": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "real": "float",
    "double precision": "double",
    "money": "decimal",
    "boolean": "boolean",
    "text": "string",
    "character varying": "string",
    "character": "string",
    "citext": "string",
    "uuid": "uriorcurie",
    "json": "string",
    "jsonb": "string",
    "xml": "string",
    "bytea": "string",
    "date": "date",
    "timestamp without time zone": "datetime",
    "timestamp with time zone": "datetime",
    "time without time zone": "time",
    "time with time zone": "time",
    "interval": "string",
    "inet": "string",
    "cidr": "string",
    "macaddr": "string",
}

DEFAULT_RANGE = "string"


def normalise_type(pg_type: str) -> tuple[str, bool]:
    """Return (linkml_range, multivalued) for a Postgres type string.

    ``format_type`` output carries length and precision (``character varying(255)``)
    and array markers (``integer[]``); both are stripped before lookup.
    """
    raw = (pg_type or "").strip().lower()
    multivalued = raw.endswith("[]")
    if multivalued:
        raw = raw[:-2].strip()
    base = re.sub(r"\(.*?\)", "", raw).strip()
    return TYPE_MAP.get(base, DEFAULT_RANGE), multivalued


def class_name(table: str) -> str:
    """analysis_project -> AnalysisProject (LinkML classes are UpperCamelCase)."""
    parts = [p for p in re.split(r"[^0-9a-zA-Z]+", table) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or table


def refine(
    schema: dict[str, Any],
    metadata: dict[str, Any],
    name: str | None = None,
    schema_id: str | None = None,
    camel_case: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Return (refined_schema, report_lines). The input schema is not mutated.

    With ``camel_case``, classes are renamed to LinkML's UpperCamelCase convention and
    the source table name is kept as an alias. Foreign-key ranges are remapped to match,
    so references stay resolvable.
    """
    result = dict(schema)
    classes = {k: dict(v) for k, v in (result.get("classes") or {}).items()}
    class_names = set(classes)

    # Built from the original names, since that is what ranges in the input refer to.
    renames = {n: class_name(n) for n in class_names} if camel_case else {n: n for n in class_names}
    collisions = len(set(renames.values())) != len(renames)
    if collisions:  # two tables mapping to one class would silently merge them
        renames = {n: n for n in class_names}

    columns: dict[tuple[str, str], dict] = {
        (c["table_name"], c["column_name"]): c for c in metadata.get("columns", [])
    }
    table_comments = {
        t["table_name"]: t.get("comment") for t in metadata.get("tables", [])
    }

    retyped = 0
    required = 0
    multivalued = 0
    described = 0
    unmatched = 0
    preserved_links = 0

    renamed_classes: dict[str, Any] = {}
    for table_name, cls in classes.items():
        attributes = {k: dict(v) for k, v in (cls.get("attributes") or {}).items()}
        for attr_name, attr in attributes.items():
            if attr.get("range") in class_names:
                # A resolved foreign key. Retarget it, but never retype it.
                attr["range"] = renames[attr["range"]]
                preserved_links += 1
                continue

            col = columns.get((table_name, attr_name))
            if col is None:
                unmatched += 1
                continue

            rng, is_array = normalise_type(col.get("data_type", ""))
            if rng != attr.get("range"):
                attr["range"] = rng
                retyped += 1
            if is_array and not attr.get("multivalued"):
                attr["multivalued"] = True
                multivalued += 1
            # An identifier is required by definition; saying so again is noise.
            if col.get("not_null") and not attr.get("identifier"):
                attr["required"] = True
                required += 1
            if col.get("comment") and not attr.get("description"):
                attr["description"] = col["comment"]
                described += 1

        cls["attributes"] = attributes
        if table_comments.get(table_name) and not cls.get("description"):
            cls["description"] = table_comments[table_name]
        new_name = renames[table_name]
        if new_name != table_name:
            # Keep the source table name discoverable after renaming.
            aliases = list(cls.get("aliases") or [])
            if table_name not in aliases:
                aliases.append(table_name)
            cls["aliases"] = aliases
        renamed_classes[new_name] = cls

    classes = renamed_classes

    schema_name = name or f"{metadata.get('schema', 'schema')}"
    base = schema_id or f"https://w3id.org/jgi/{schema_name}"

    # Rebuilt in a deterministic order so diffs between runs stay readable.
    refined = {
        "id": base,
        "name": schema_name,
        "title": f"{metadata.get('catalog')}.{metadata.get('schema')}",
        "description": (
            f"Generated from {metadata.get('catalog')}.{metadata.get('schema')} by "
            "schema-automator, with types and nullability refined from native "
            "catalog metadata by lakehouse-schema-extraction."
        ),
        "prefixes": {
            "linkml": "https://w3id.org/linkml/",
            schema_name: f"{base}/",
        },
        "default_prefix": schema_name,
        "default_range": DEFAULT_RANGE,
        "imports": ["linkml:types"],
        "classes": classes,
    }
    for key in ("enums", "slots", "types", "subsets"):
        if schema.get(key):
            refined[key] = schema[key]

    report = [
        f"{len(classes)} classes",
        f"{preserved_links} foreign-key object ranges preserved",
    ]
    if camel_case and collisions:
        report.append("class names left as-is: CamelCase conversion would collide")
    elif camel_case:
        report.append(f"{len(renames)} classes renamed to UpperCamelCase (table kept as alias)")
    report += [
        f"{retyped} slot ranges corrected from native types",
        f"{required} slots marked required from NOT NULL",
    ]
    if multivalued:
        report.append(f"{multivalued} array slots marked multivalued")
    if described:
        report.append(f"{described} descriptions added from column comments")
    if unmatched:
        report.append(f"{unmatched} slots had no matching column and were left as-is")
    if not any(table_comments.values()):
        report.append("source database has no table comments: classes have no descriptions")
    return refined, report
