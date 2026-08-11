"""CLI: generate docs/index.md and mkdocs.yml from the promoted LinkML schemas."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from lakehouse_schema_extraction import __version__
from lakehouse_schema_extraction.docs_site import (
    collect,
    normalise_repo_url,
    render_index,
    render_mkdocs_config,
)

# Used only when the git remote cannot be read (e.g. a tarball export).
FALLBACK_REPO = "https://github.com/sierra-moxon/lakehouse-schema-extraction"


def detect_repo_url() -> str:
    """Read the origin remote so the site's repository link is never a stale guess."""
    try:
        raw = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return FALLBACK_REPO
    return normalise_repo_url(raw) or FALLBACK_REPO


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-s", "--schema-dir", type=click.Path(file_okay=False, path_type=Path),
              default=Path("schemas"), show_default=True,
              help="Directory of promoted <catalog>/<schema>.linkml.yaml files.")
@click.option("-d", "--docs-dir", type=click.Path(file_okay=False, path_type=Path),
              default=Path("docs"), show_default=True, help="MkDocs docs directory.")
@click.option("-c", "--config", type=click.Path(dir_okay=False, path_type=Path),
              default=Path("mkdocs.yml"), show_default=True, help="mkdocs.yml to write.")
@click.option("--repo-url", default=None,
              help="Repository URL shown in the site header. "
                   "[default: the origin remote]")
@click.option("--only", default=None, metavar="CATALOG/SCHEMA",
              help="Restrict the site to a single schema, for a quick local preview.")
@click.version_option(__version__)
def main(schema_dir: Path, docs_dir: Path, config: Path, repo_url: str | None,
         only: str | None) -> None:
    """Write the site index and navigation, derived from the schemas on disk."""
    repo_url = repo_url or detect_repo_url()
    rows = collect(schema_dir)

    if only:
        wanted = only.strip("/")
        rows = [r for r in rows if f"{r['catalog']}/{r['schema']}" == wanted]
        if not rows:
            available = ", ".join(
                sorted(f"{r['catalog']}/{r['schema']}" for r in collect(schema_dir))[:5]
            )
            raise click.ClickException(
                f"no schema matching {only!r}; try one of: {available}, ..."
            )
    if not rows:
        raise click.ClickException(
            f"no schemas found under {schema_dir}/<catalog>/<schema>.linkml.yaml -- "
            "run `just promote` after `just linkml-all`"
        )

    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "index.md").write_text(render_index(rows))
    config.write_text(render_mkdocs_config(rows, repo_url))

    missing = [r for r in rows if not (docs_dir / r["doc_path"]).exists()]

    catalogs = {r["catalog"] for r in rows}
    click.echo(f"  {len(rows)} schemas across {len(catalogs)} databases", err=True)
    click.echo(f"  {sum(r['classes'] for r in rows)} classes, "
               f"{sum(r['links'] for r in rows)} references", err=True)
    if missing:
        click.echo(f"  {len(missing)} without generated pages yet "
                   "(run `just gendoc`)", err=True)
    click.echo(f"wrote {docs_dir / 'index.md'} and {config}", err=True)


if __name__ == "__main__":
    main()
