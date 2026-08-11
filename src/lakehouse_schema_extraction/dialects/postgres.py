"""PostgreSQL dialect: native ``pg_catalog`` extraction and DDL rendering."""

from __future__ import annotations

from lakehouse_schema_extraction.dialects.base import (
    SchemaMetadata,
    quote_ident,
    quote_literal,
)

# pg_constraint.contype -> portable constraint type
CONTYPE = {
    "p": "PRIMARY KEY",
    "f": "FOREIGN KEY",
    "u": "UNIQUE",
    "c": "CHECK",
    "x": "EXCLUDE",
}

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

Q_SCHEMAS = """
SELECT nspname AS schema_name
FROM pg_namespace
WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND nspname NOT LIKE 'pg_temp%' AND nspname NOT LIKE 'pg_toast_temp%'
ORDER BY nspname
"""

Q_TABLES = """
SELECT c.relname AS table_name,
       c.relkind AS relkind,
       obj_description(c.oid) AS comment,
       c.reltuples::bigint AS approx_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relname
"""

Q_COLUMNS = """
SELECT c.relname AS table_name,
       a.attnum AS ordinal_position,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       col_description(c.oid, a.attnum) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

# Column lists are aggregated to comma-separated text rather than returned as arrays:
# array types add a type-mapping dependency in the connector for no benefit here.
Q_CONSTRAINTS = """
SELECT c.relname AS table_name,
       con.conname AS constraint_name,
       con.contype AS contype,
       pg_get_constraintdef(con.oid) AS definition,
       tgt.relname AS target_table,
       (SELECT string_agg(att.attname, ',' ORDER BY u.ord)
          FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = u.attnum) AS column_list,
       (SELECT string_agg(att.attname, ',' ORDER BY u.ord)
          FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att
            ON att.attrelid = con.confrelid AND att.attnum = u.attnum) AS target_column_list
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class tgt ON tgt.oid = con.confrelid
WHERE n.nspname = '{schema}'
ORDER BY c.relname, con.contype, con.conname
"""

Q_INDEXES = """
SELECT tablename AS table_name, indexname AS index_name, indexdef AS definition
FROM pg_indexes WHERE schemaname = '{schema}'
ORDER BY tablename, indexname
"""

# data_type is a regtype, which the Trino connector cannot map (jdbcType 1111/OTHER)
# and which fails the whole query. Catalog columns of type regtype, oid, name, or
# pg_node_tree must be cast to text before they cross the connector boundary.
# Extensions are database-wide, not schema-scoped, but indexes in the schema can depend
# on operator classes they provide (GOLD uses gin_trgm_ops from pg_trgm). Without these
# the index statements fail on a fresh database.
Q_EXTENSIONS = """
SELECT e.extname AS extension_name, n.nspname AS schema_name
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname <> 'plpgsql'
ORDER BY e.extname
"""

Q_SEQUENCES = """
SELECT sequencename AS sequence_name,
       data_type::text AS data_type,
       start_value,
       increment_by
FROM pg_sequences WHERE schemaname = '{schema}' ORDER BY sequencename
"""

# pg_views omits materialized views; they live in pg_matviews and would otherwise be
# dropped from the output entirely (they are not in pg_class-derived column queries
# either, since those are restricted to ordinary and partitioned tables).
Q_VIEWS = """
SELECT viewname AS view_name, definition, 'view' AS view_kind
FROM pg_views WHERE schemaname = '{schema}'
UNION ALL
SELECT matviewname AS view_name, definition, 'materialized' AS view_kind
FROM pg_matviews WHERE schemaname = '{schema}'
ORDER BY 1
"""

# Views built on other views must be created after their dependencies. Edges come from
# pg_rewrite: a view's rewrite rule depends on every relation the view selects from.
Q_VIEW_DEPS = """
SELECT DISTINCT dependent.relname AS view_name, referenced.relname AS depends_on
FROM pg_depend d
JOIN pg_rewrite r ON r.oid = d.objid
JOIN pg_class dependent ON dependent.oid = r.ev_class
JOIN pg_class referenced ON referenced.oid = d.refobjid
JOIN pg_namespace dn ON dn.oid = dependent.relnamespace
JOIN pg_namespace rn ON rn.oid = referenced.relnamespace
WHERE d.classid = 'pg_rewrite'::regclass
  AND d.refclassid = 'pg_class'::regclass
  AND dependent.oid <> referenced.oid
  AND referenced.relkind IN ('v', 'm')
  AND dn.nspname = '{schema}' AND rn.nspname = '{schema}'
"""


def _split(value: str | None) -> list[str]:
    return [p for p in (value or "").split(",") if p]


def order_views(views: list[dict], edges: list[dict]) -> list[dict]:
    """Sort views so each is created after the views it selects from.

    Kept pure and total: unknown or cyclic dependencies degrade to the original
    order rather than raising, since a partially-ordered dump is more useful than none.
    """
    by_name = {v["view_name"]: v for v in views}
    deps = {name: set() for name in by_name}
    for edge in edges:
        view, on = edge["view_name"], edge["depends_on"]
        if view in by_name and on in by_name and view != on:
            deps[view].add(on)

    ordered: list[dict] = []
    emitted: set[str] = set()
    remaining = [v["view_name"] for v in views]
    while remaining:
        ready = [n for n in remaining if deps[n] <= emitted]
        if not ready:  # dependency cycle: emit the rest in original order
            ordered.extend(by_name[n] for n in remaining)
            break
        for name in ready:
            ordered.append(by_name[name])
            emitted.add(name)
        remaining = [n for n in remaining if n not in emitted]
    return ordered


class PostgresDialect:
    name = "postgresql"
    connector_names = ("postgresql",)

    def list_schemas(self, client) -> list[str]:
        rows = client.passthrough(Q_SCHEMAS)
        return [r["schema_name"] for r in rows]

    def extract(self, client, schema: str) -> SchemaMetadata:
        def run(template: str) -> list[dict]:
            return client.passthrough(template.format(schema=quote_literal(schema)))

        constraints = []
        for row in run(Q_CONSTRAINTS):
            constraints.append(
                {
                    "table_name": row["table_name"],
                    "constraint_name": row["constraint_name"],
                    "constraint_type": CONTYPE.get(row["contype"], row["contype"]),
                    "definition": row["definition"],
                    "target_table": row["target_table"],
                    "columns": _split(row["column_list"]),
                    "target_columns": _split(row["target_column_list"]),
                }
            )

        return SchemaMetadata(
            catalog=client.catalog,
            schema=schema,
            dialect=self.name,
            tables=run(Q_TABLES),
            columns=run(Q_COLUMNS),
            constraints=constraints,
            indexes=run(Q_INDEXES),
            views=order_views(run(Q_VIEWS), run(Q_VIEW_DEPS)),
            sequences=run(Q_SEQUENCES),
            extensions=run(Q_EXTENSIONS),
        )

    def render_ddl(self, meta: SchemaMetadata) -> str:
        schema = meta.schema
        qs = quote_ident(schema)

        cols_by_table: dict[str, list[dict]] = {}
        for col in meta.columns:
            cols_by_table.setdefault(col["table_name"], []).append(col)
        cons_by_table: dict[str, list[dict]] = {}
        for con in meta.constraints:
            cons_by_table.setdefault(con["table_name"], []).append(con)

        # Indexes that merely implement a PK/UNIQUE constraint are created implicitly
        # by the constraint itself; re-issuing them would be a duplicate.
        implicit = {
            c["constraint_name"]
            for c in meta.constraints
            if c["constraint_type"] in ("PRIMARY KEY", "UNIQUE")
        }

        out = [
            f"-- DDL for {meta.catalog}.{schema} ({meta.dialect})",
            "-- generated by lakehouse-schema-extraction from native pg_catalog metadata",
            "",
            f"CREATE SCHEMA IF NOT EXISTS {qs};",
            "",
        ]

        if meta.extensions:
            out.append("-- extensions (index operator classes depend on these).")
            out.append("-- Some may be unavailable in a stock image; those errors are")
            out.append("-- expected and harmless unless a table or index actually uses them.")
            for ext in meta.extensions:
                out.append(
                    f"CREATE EXTENSION IF NOT EXISTS {quote_ident(ext['extension_name'])};"
                )
            out.append("")

        if meta.sequences:
            out.append("-- sequences (must precede tables: serial columns default to nextval)")
            for seq in meta.sequences:
                name = quote_ident(seq["sequence_name"])
                out.append(f"CREATE SEQUENCE IF NOT EXISTS {qs}.{name};")
            out.append("")

        for table in meta.tables:
            name = table["table_name"]
            if name not in cols_by_table:
                continue  # views are emitted separately
            lines = []
            for col in cols_by_table[name]:
                piece = f"    {quote_ident(col['column_name'])} {col['data_type']}"
                if col["default_expr"]:
                    piece += f" DEFAULT {col['default_expr']}"
                if col["not_null"]:
                    piece += " NOT NULL"
                lines.append(piece)
            # PK/UNIQUE/CHECK inline; FKs deferred so table load order does not matter.
            for con in cons_by_table.get(name, []):
                if con["constraint_type"] in ("PRIMARY KEY", "UNIQUE", "CHECK"):
                    lines.append(
                        f"    CONSTRAINT {quote_ident(con['constraint_name'])} {con['definition']}"
                    )
            out.append(f"CREATE TABLE {qs}.{quote_ident(name)} (")
            out.append(",\n".join(lines))
            out.append(");")
            out.append("")

        foreign_keys = meta.foreign_keys
        if foreign_keys:
            out.append("-- foreign keys (deferred: schemas of this age contain reference cycles)")
            for con in foreign_keys:
                out.append(
                    f"ALTER TABLE {qs}.{quote_ident(con['table_name'])} "
                    f"ADD CONSTRAINT {quote_ident(con['constraint_name'])} {con['definition']};"
                )
            out.append("")

        # pg_indexes covers materialized views too, and those indexes cannot be created
        # until the matview exists -- so they are held back until after the view section.
        relkinds = {t["table_name"]: t.get("relkind", "r") for t in meta.tables}
        extra = [i for i in meta.indexes if i["index_name"] not in implicit]
        table_indexes = [i for i in extra if relkinds.get(i["table_name"], "r") in ("r", "p")]
        view_indexes = [i for i in extra if relkinds.get(i["table_name"], "r") in ("v", "m")]

        if table_indexes:
            out.append("-- indexes")
            for idx in table_indexes:
                out.append(idx["definition"] + ";")
            out.append("")

        if meta.views:
            out.append("-- views (ordered so view-on-view dependencies resolve)")
            for view in meta.views:
                name = quote_ident(view["view_name"])
                if view.get("view_kind") == "materialized":
                    # No OR REPLACE form exists for materialized views.
                    out.append(f"CREATE MATERIALIZED VIEW {qs}.{name} AS")
                else:
                    out.append(f"CREATE OR REPLACE VIEW {qs}.{name} AS")
                out.append(view["definition"].strip().rstrip(";") + ";")
                out.append("")

        if view_indexes:
            out.append("-- indexes on materialized views (must follow their view)")
            for idx in view_indexes:
                out.append(idx["definition"] + ";")
            out.append("")

        comments = []
        for table in meta.tables:
            if table.get("comment"):
                comments.append(
                    f"COMMENT ON TABLE {qs}.{quote_ident(table['table_name'])} "
                    f"IS '{quote_literal(table['comment'])}';"
                )
        for col in meta.columns:
            if col.get("comment"):
                comments.append(
                    f"COMMENT ON COLUMN {qs}.{quote_ident(col['table_name'])}"
                    f".{quote_ident(col['column_name'])} IS '{quote_literal(col['comment'])}';"
                )
        if comments:
            out.append("-- comments")
            out.extend(comments)
            out.append("")

        return "\n".join(out)
