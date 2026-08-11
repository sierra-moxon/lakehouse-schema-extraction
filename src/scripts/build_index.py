"""CLI: build an index page over everything a sweep has produced."""

from __future__ import annotations

from pathlib import Path

import click

from lakehouse_schema_extraction import __version__
from lakehouse_schema_extraction.index_page import collect, render


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-d", "--out-dir", type=click.Path(file_okay=False, path_type=Path),
              default=Path("out"), show_default=True,
              help="Directory holding <catalog>/<schema>.json extractions.")
@click.version_option(__version__)
def main(out_dir: Path) -> None:
    """Write OUT_DIR/index.html summarising every extracted schema."""
    rows = collect(out_dir)
    if not rows:
        raise click.ClickException(
            f"no extractions found under {out_dir}/<catalog>/<schema>.json -- "
            "run `just extract-all` first"
        )

    target = out_dir / "index.html"
    target.write_text(render(rows, out_dir))

    missing_erd = [r for r in rows if not r["schemaspy"]]
    no_fks = [r for r in rows if r["foreign_keys"] == 0]

    click.echo(f"  {len(rows)} schemas indexed", err=True)
    if missing_erd:
        click.echo(f"  {len(missing_erd)} without an ERD yet "
                   "(run `just schemaspy <catalog> <schema>`)", err=True)
    if no_fks:
        names = ", ".join(f"{r['catalog']}.{r['schema']}" for r in no_fks[:4])
        more = "" if len(no_fks) <= 4 else f", +{len(no_fks) - 4} more"
        click.echo(f"  {len(no_fks)} declare no foreign keys: {names}{more}", err=True)
    click.echo(f"wrote {target}", err=True)


if __name__ == "__main__":
    main()
