"""CLI for extracting DDL and schema metadata from lakehouse-federated databases."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from lakehouse_schema_extraction import __version__
from lakehouse_schema_extraction.client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LakehouseClient,
    list_catalogs,
)
from lakehouse_schema_extraction.dialects import (
    UnsupportedDialectError,
    get_dialect,
    registered_dialects,
)
from lakehouse_schema_extraction.sweep import discover

host_option = click.option(
    "--host", default=DEFAULT_HOST, show_default=True, help="Lakehouse host."
)
port_option = click.option(
    "--port", default=DEFAULT_PORT, show_default=True, help="Lakehouse port."
)


def _resolve_connector(catalog: str, host: str, port: int) -> str | None:
    """Look up a catalog's connector name, tolerating a user without system-table access."""
    try:
        for row in list_catalogs(host=host, port=port):
            if row["catalog_name"] == catalog:
                return row["connector_name"]
    except Exception as exc:  # noqa: BLE001 - fall back to the name-suffix hint
        click.echo(f"note: could not read system.metadata.catalogs ({exc}); "
                   "falling back to catalog-name suffix", err=True)
    return None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
def cli() -> None:
    """Extract schema metadata from databases behind the Starburst lakehouse.

    Authentication is OAuth2 (a browser opens on first use) unless TRINO_PASSWORD is
    set, in which case basic auth is used.
    """


@cli.command("catalogs")
@host_option
@port_option
@click.option("--supported/--all", "supported_only", default=False,
              help="Show only catalogs a dialect can handle.")
def catalogs_cmd(host: str, port: int, supported_only: bool) -> None:
    """List catalogs and their connector types."""
    rows = list_catalogs(host=host, port=port)
    known = set(registered_dialects())
    for row in rows:
        handled = row["connector_name"] in known
        if supported_only and not handled:
            continue
        mark = "+" if handled else " "
        click.echo(f"{mark} {row['catalog_name']:<40} {row['connector_name']}")
    if not supported_only:
        click.echo(f"\n('+' = supported dialect: {', '.join(sorted(known))})", err=True)


@cli.command("targets")
@host_option
@port_option
@click.option("--all", "include_all", is_flag=True,
              help="Include schemas the skip rules would drop.")
@click.option("--format", "fmt", type=click.Choice(["tsv", "table", "json"]),
              default="tsv", show_default=True,
              help="tsv is meant for shell loops; table is for reading.")
def targets_cmd(host: str, port: int, include_all: bool, fmt: str) -> None:
    """List every (catalog, schema) pair a sweep would process."""
    found = discover(host, port, include_all=include_all)

    if fmt == "json":
        click.echo(json.dumps(
            {"selected": [t.to_dict() for t in found.selected],
             "skipped": [t.to_dict() for t in found.skipped],
             "errors": found.errors},
            indent=2))
        return

    if fmt == "tsv":
        # Consumed by `just` loops: catalog<TAB>schema, nothing else on stdout.
        for target in found.selected:
            click.echo(f"{target.catalog}\t{target.schema}")
    else:
        for target in found.selected:
            click.echo(f"  {target.catalog:<28} {target.schema:<22} "
                       f"{target.table_count:>5} tables {target.view_count:>4} views")

    # Skips and errors go to stderr so they never pollute a shell loop, but are
    # always shown: a silently dropped schema is indistinguishable from a missing one.
    for target in found.skipped:
        click.echo(f"skip {target.catalog}.{target.schema}: {target.skipped}", err=True)
    for err in found.errors:
        click.echo(f"ERROR {err['catalog']}: {err['error']}", err=True)
    click.echo(
        f"{len(found.selected)} selected, {len(found.skipped)} skipped, "
        f"{len(found.errors)} unreachable",
        err=True,
    )


@cli.command("schemas")
@click.argument("catalog")
@host_option
@port_option
def schemas_cmd(catalog: str, host: str, port: int) -> None:
    """List user schemas in CATALOG."""
    connector = _resolve_connector(catalog, host, port)
    dialect = get_dialect(connector, catalog)
    with LakehouseClient(catalog=catalog, host=host, port=port) as client:
        for name in dialect.list_schemas(client):
            click.echo(name)


@cli.command("extract")
@click.argument("catalog")
@click.argument("schema")
@host_option
@port_option
@click.option("-o", "--out-dir", type=click.Path(path_type=Path), default=Path("out"),
              show_default=True, help="Directory for generated files.")
@click.option("--prefix", default=None,
              help="Output filename stem. [default: <catalog>.<schema>]")
@click.option("--sql/--no-sql", default=True, help="Write DDL .sql output.")
@click.option("--json/--no-json", "want_json", default=True,
              help="Write structured .json metadata.")
def extract_cmd(catalog: str, schema: str, host: str, port: int, out_dir: Path,
                prefix: str | None, sql: bool, want_json: bool) -> None:
    """Extract DDL and metadata for CATALOG.SCHEMA."""
    connector = _resolve_connector(catalog, host, port)
    try:
        dialect = get_dialect(connector, catalog)
    except UnsupportedDialectError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"extracting {catalog}.{schema} using the {dialect.name} dialect", err=True)

    with LakehouseClient(catalog=catalog, schema=schema, host=host, port=port) as client:
        meta = dialect.extract(client, schema)

    click.echo(
        f"  {len(meta.tables)} relations, {len(meta.columns)} columns, "
        f"{len(meta.primary_keys)} primary keys, {len(meta.foreign_keys)} foreign keys, "
        f"{len(meta.indexes)} indexes",
        err=True,
    )
    if not meta.foreign_keys:
        click.echo(
            "  WARNING: no foreign keys declared in the database. Relationship-aware "
            "tooling (SchemaSpy ERDs, LinkML ranges) will have nothing to draw.",
            err=True,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = prefix or f"{catalog}.{schema}"

    if sql:
        path = out_dir / f"{stem}.sql"
        path.write_text(dialect.render_ddl(meta))
        click.echo(f"wrote {path}", err=True)

    if want_json:
        path = out_dir / f"{stem}.json"
        path.write_text(json.dumps(meta.to_dict(), indent=2, default=str))
        click.echo(f"wrote {path}", err=True)


def main() -> None:
    try:
        cli()
    except UnsupportedDialectError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
