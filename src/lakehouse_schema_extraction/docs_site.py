"""Generate the MkDocs index page and navigation from the promoted LinkML schemas.

bridge-schemas maintains its ``nav:`` and index tables by hand. With ~24 schemas across
five catalogs that does not scale, and a hand-written table drifts from the schemas the
moment one is regenerated. Everything here is derived from the schema files themselves,
so the site cannot claim a schema, a class count, or a link that is not real.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Display names for catalogs. Anything unlisted falls back to a tidied catalog name,
# so a newly federated database appears in the site without a code change.
# MkDocs needs a literal `!!python/name:` tag here, which yaml.safe_dump refuses to
# emit. The config carries a placeholder that is substituted into the dumped text.
#
# This mirrors bridge-schemas, which publishes LinkML gen-doc output successfully:
# the mermaid2 plugin injects the library and provides the fence formatter. Material's
# own mermaid support would also work, but it pulls mermaid from unpkg at page load,
# so the diagrams break for anyone behind a restrictive network.
MERMAID_FORMAT = "__MERMAID_FENCE_FORMAT__"
MERMAID_FENCE_TAG = "!!python/name:mermaid2.fence_mermaid"

CATALOG_LABELS = {
    "gold-db-2_postgresql": "GOLD",
    "img-db-2_postgresql": "IMG",
    "plant-db-7_postgresql": "Phytozome / Plant",
    "smc-db_postgresql": "SMC",
    "gcs-vm-1_postgresql": "Citation Service",
}

CATALOG_DESCRIPTIONS = {
    "gold-db-2_postgresql": "Genomes OnLine Database: studies, biosamples, sequencing "
                            "and analysis projects.",
    "img-db-2_postgresql": "Integrated Microbial Genomes: core, extended, satellite and "
                           "submission schemas.",
    "plant-db-7_postgresql": "Plant genomics: current and historical Phytozome schemas.",
    "smc-db_postgresql": "Secondary Metabolite Collaboratory: biosynthetic gene clusters.",
    "gcs-vm-1_postgresql": "Genome Citation Service.",
}

# Ordered so the largest, most-used databases lead the page.
CATALOG_ORDER = [
    "gold-db-2_postgresql",
    "img-db-2_postgresql",
    "plant-db-7_postgresql",
    "smc-db_postgresql",
    "gcs-vm-1_postgresql",
]


def normalise_repo_url(raw: str) -> str | None:
    """Turn any git remote form into a browsable https URL.

    Handles ``git@github.com:user/repo.git``, ``ssh://git@github.com/user/repo.git``
    and ``https://github.com/user/repo.git``. Returns None if it cannot be parsed,
    so callers fall back rather than publishing a broken link.
    """
    url = (raw or "").strip()
    if not url:
        return None
    url = re.sub(r"^ssh://", "", url)
    # scp-style `git@host:user/repo`, and `git@host/user/repo` left by stripping ssh://
    match = re.match(r"^[\w.-]+@([\w.-]+)[:/](.+)$", url)
    if match:
        url = f"https://{match.group(1)}/{match.group(2)}"
    elif url.startswith("git://"):
        url = "https://" + url[len("git://"):]
    if not url.startswith(("http://", "https://")):
        return None
    return url.removesuffix(".git").rstrip("/")


def pages_url(repo_url: str) -> str | None:
    """Derive the GitHub Pages URL for a repository, used for canonical links."""
    match = re.match(r"^https?://github\.com/([^/]+)/([^/]+)$", repo_url or "")
    if not match:
        return None
    owner, repo = match.groups()
    return f"https://{owner.lower()}.github.io/{repo}/"


def catalog_label(catalog: str) -> str:
    if catalog in CATALOG_LABELS:
        return CATALOG_LABELS[catalog]
    stem = re.sub(r"_(postgresql|mysql|mariadb)$", "", catalog)
    return stem.replace("-", " ").replace("_", " ").title()


def schema_label(schema: str) -> str:
    return schema.replace("_", " ")


def summarise(path: Path) -> dict[str, Any]:
    """Read a LinkML schema and count what the docs page will show."""
    data = yaml.safe_load(path.read_text()) or {}
    classes = data.get("classes") or {}
    class_names = set(classes)

    slots = 0
    links = 0
    required = 0
    for cls in classes.values():
        attributes = cls.get("attributes") or {}
        slots += len(attributes)
        for attr in attributes.values():
            if attr.get("range") in class_names:
                links += 1
            if attr.get("required"):
                required += 1

    return {
        "name": data.get("name") or path.stem,
        "title": data.get("title") or "",
        "classes": len(classes),
        "slots": slots,
        "links": links,
        "required": required,
    }


def collect(schema_dir: Path) -> list[dict[str, Any]]:
    """Find every promoted schema as <schema_dir>/<catalog>/<schema>.linkml.yaml."""
    rows = []
    for path in sorted(schema_dir.glob("*/*.linkml.yaml")):
        catalog = path.parent.name
        schema = path.name.removesuffix(".linkml.yaml")
        rows.append({
            "catalog": catalog,
            "schema": schema,
            "path": path,
            "doc_path": f"{catalog}/{schema}/index.md",
            **summarise(path),
        })
    return rows


def _ordered_catalogs(rows: list[dict]) -> list[str]:
    present = {r["catalog"] for r in rows}
    ordered = [c for c in CATALOG_ORDER if c in present]
    return ordered + sorted(present - set(ordered))


def render_index(rows: list[dict[str, Any]]) -> str:
    total_classes = sum(r["classes"] for r in rows)
    total_links = sum(r["links"] for r in rows)

    out = [
        "# JGI Lakehouse Schemas",
        "",
        "LinkML schemas generated from the relational databases federated behind the ",
        "JGI Starburst lakehouse.",
        "",
        f"**{len(rows)} schemas** across **{len(_ordered_catalogs(rows))} databases** — ",
        f"{total_classes} classes and {total_links} foreign-key references.",
        "",
        "Each schema is extracted from the source database's native catalog, so the ",
        "class structure, primary keys, and relationships reflect what the database ",
        "actually declares rather than what can be inferred through a query engine.",
        "",
    ]

    for catalog in _ordered_catalogs(rows):
        entries = sorted([r for r in rows if r["catalog"] == catalog],
                         key=lambda r: -r["classes"])
        out.append(f"## {catalog_label(catalog)}")
        out.append("")
        if catalog in CATALOG_DESCRIPTIONS:
            out.append(CATALOG_DESCRIPTIONS[catalog])
            out.append("")
        out.append(f"`{catalog}`")
        out.append("")
        out.append("| Schema | Classes | Slots | References | Required |")
        out.append("|--------|--------:|------:|-----------:|---------:|")
        for row in entries:
            links = str(row["links"]) if row["links"] else "—"
            out.append(
                f"| [{row['schema']}]({row['doc_path']}) | {row['classes']} | "
                f"{row['slots']} | {links} | {row['required']} |"
            )
        out.append("")
        no_links = [r for r in entries if not r["links"]]
        if no_links:
            names = ", ".join(f"`{r['schema']}`" for r in no_links)
            out.append(
                f"!!! note \"No declared relationships\"\n"
                f"    {names} declare no foreign keys in the source database, so their "
                f"classes have no object-valued slots. This reflects the database, not "
                f"the extraction."
            )
            out.append("")

    out += [
        "## How these were generated",
        "",
        "1. Schema metadata is read from each database's native catalog through the ",
        "   lakehouse's SQL pass-through, because Trino's `information_schema` exposes ",
        "   no primary keys, foreign keys, or indexes.",
        "2. The resulting DDL is loaded into a throwaway PostgreSQL instance holding ",
        "   structure only — no data leaves the source databases.",
        "3. `schema-automator` introspects that instance to resolve foreign keys into ",
        "   object-valued slots.",
        "4. A refinement pass restores scalar types, nullability, and naming from the ",
        "   native catalog metadata, which SQLAlchemy reflection loses.",
        "",
        "See the [repository](https://github.com/) for the pipeline.",
        "",
    ]
    return "\n".join(out)


def render_mkdocs_config(rows: list[dict[str, Any]], repo_url: str) -> str:
    """Build mkdocs.yml. Generated wholesale: the nav is derived, not curated."""
    nav: list[Any] = [{"Home": "index.md"}]
    for catalog in _ordered_catalogs(rows):
        entries = sorted([r for r in rows if r["catalog"] == catalog],
                         key=lambda r: -r["classes"])
        nav.append({
            catalog_label(catalog): [
                {schema_label(r["schema"]): r["doc_path"]} for r in entries
            ]
        })

    config = {
        "site_name": "JGI Lakehouse Schemas",
        "site_description": "LinkML schemas generated from databases federated behind "
                            "the JGI Starburst lakehouse",
        "repo_url": repo_url,
        # Only affects canonical tags and sitemap.xml; page links stay relative.
        **({"site_url": pages_url(repo_url)} if pages_url(repo_url) else {}),
        # Theme, plugins and extensions mirror bridge-schemas, the reference LinkML
        # documentation site, so this stays close to established practice.
        "theme": {
            "name": "material",
            "palette": {"primary": "teal", "accent": "cyan"},
        },
        "plugins": ["search", "mermaid2"],
        "markdown_extensions": [
            # admonition backs the `!!! note` blocks on the generated index page.
            "admonition",
            # Required, not optional, and the one addition beyond bridge-schemas:
            # gen-doc wraps every page body in `<div data-search-exclude markdown="1">`.
            # Without md_in_html, Python-Markdown treats that div's contents as raw
            # HTML and parses none of it, so headings, tables, links and mermaid
            # fences all render as literal text.
            "md_in_html",
            # gen-doc emits ```mermaid class diagrams. Without this custom fence,
            # superfences treats them as ordinary code blocks.
            {"pymdownx.superfences": {
                "custom_fences": [{
                    "name": "mermaid",
                    "class": "mermaid",
                    "format": MERMAID_FORMAT,
                }]
            }},
        ],
        "nav": nav,
    }

    header = (
        "# GENERATED FILE -- do not edit by hand.\n"
        "# Rebuild with `just docs-index`; the nav is derived from schemas/.\n"
    )
    body = yaml.safe_dump(config, sort_keys=False, width=100, allow_unicode=True)
    # safe_dump cannot emit a `!!python/name:` tag, and MkDocs requires that exact form
    # for the fence formatter, so the placeholder is swapped for it after dumping.
    # safe_dump may or may not quote the placeholder depending on its content, so both
    # forms are handled; the quoted one must go first or it would leave stray quotes.
    body = body.replace(f"'{MERMAID_FORMAT}'", MERMAID_FENCE_TAG)
    return header + body.replace(MERMAID_FORMAT, MERMAID_FENCE_TAG)
