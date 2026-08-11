"""CLI: refine a schema-automator LinkML schema with precise extracted metadata."""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml

from lakehouse_schema_extraction import __version__
from lakehouse_schema_extraction.linkml_refine import refine


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("schema_yaml", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("metadata_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Output path. [default: overwrite SCHEMA_YAML in place]")
@click.option("-n", "--name", default=None,
              help="LinkML schema name. [default: the source schema name]")
@click.option("--id", "schema_id", default=None,
              help="LinkML schema id URI. [default: https://w3id.org/jgi/<name>]")
@click.option("--camel-case/--db-names", default=True,
              help="Rename classes to UpperCamelCase, keeping the table name as an alias.")
@click.version_option(__version__)
def main(schema_yaml: Path, metadata_json: Path, output: Path | None,
         name: str | None, schema_id: str | None, camel_case: bool) -> None:
    """Refine SCHEMA_YAML using METADATA_JSON.

    SCHEMA_YAML is the output of `schemauto import-sql`; METADATA_JSON is the file
    written by `lakehouse-schema extract`. Foreign-key object ranges are preserved;
    scalar types, nullability, and schema naming are corrected.
    """
    schema = yaml.safe_load(schema_yaml.read_text())
    metadata = json.loads(metadata_json.read_text())

    refined, report = refine(schema, metadata, name=name, schema_id=schema_id,
                             camel_case=camel_case)

    target = output or schema_yaml
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(refined, sort_keys=False, width=100, allow_unicode=True)
    )

    for line in report:
        click.echo(f"  {line}", err=True)
    click.echo(f"wrote {target}", err=True)


if __name__ == "__main__":
    main()
