"""Build a single index over everything a sweep produced.

Reads only what is actually on disk, so the page never claims an artifact exists when
the run that would have produced it failed or has not happened yet.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

STYLE = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 68rem; padding: 0 1rem; line-height: 1.5; }
h1 { margin-bottom: 0.2rem; }
p.sub { color: #666; margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2.5rem; }
th, td { text-align: left; padding: 0.45rem 0.7rem; border-bottom: 1px solid #8883; }
th { font-weight: 600; font-size: 0.85rem; text-transform: uppercase;
     letter-spacing: 0.03em; color: #666; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
caption { text-align: left; font-weight: 600; font-size: 1.05rem;
          padding: 0.6rem 0; border-bottom: 2px solid #8886; }
a { color: #0366d6; } @media (prefers-color-scheme: dark) { a { color: #6cb6ff; } }
.missing { color: #999; }
.warn { color: #b45309; }
"""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def collect(out_dir: Path) -> list[dict[str, Any]]:
    """Find every extracted schema under out_dir and note which artifacts exist."""
    rows: list[dict[str, Any]] = []
    for meta_path in sorted(out_dir.glob("*/*.json")):
        catalog = meta_path.parent.name
        schema = meta_path.stem
        meta = _read_json(meta_path)
        if meta is None:
            continue

        constraints = meta.get("constraints") or []
        fks = [c for c in constraints if c.get("constraint_type") == "FOREIGN KEY"]
        tables = [t for t in (meta.get("tables") or []) if t.get("relkind") in ("r", "p")]

        linkml = meta_path.with_suffix(".linkml.yaml")
        spy = out_dir / "schemaspy" / catalog / schema / "index.html"
        sql = meta_path.with_suffix(".sql")

        rows.append({
            "catalog": meta.get("catalog", catalog),
            "catalog_dir": catalog,
            "schema": schema,
            "tables": len(tables),
            "columns": len(meta.get("columns") or []),
            "foreign_keys": len(fks),
            "views": len(meta.get("views") or []),
            "sql": sql if sql.exists() else None,
            "json": meta_path,
            "linkml": linkml if linkml.exists() else None,
            "schemaspy": spy if spy.exists() else None,
        })
    return rows


def _link(out_dir: Path, path: Path | None, label: str) -> str:
    if path is None:
        return f'<span class="missing">{label}</span>'
    rel = path.relative_to(out_dir)
    return f'<a href="{html.escape(str(rel))}">{label}</a>'


def render(rows: list[dict[str, Any]], out_dir: Path) -> str:
    by_catalog: dict[str, list[dict]] = {}
    for row in rows:
        by_catalog.setdefault(row["catalog"], []).append(row)

    totals = {
        "schemas": len(rows),
        "tables": sum(r["tables"] for r in rows),
        "foreign_keys": sum(r["foreign_keys"] for r in rows),
        "erds": sum(1 for r in rows if r["schemaspy"]),
    }

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Lakehouse schema documentation</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>Lakehouse schema documentation</h1>",
        f"<p class='sub'>{totals['schemas']} schemas &middot; "
        f"{totals['tables']} tables &middot; {totals['foreign_keys']} foreign keys "
        f"&middot; {totals['erds']} ERDs built</p>",
    ]

    for catalog in sorted(by_catalog):
        parts.append("<table>")
        parts.append(f"<caption>{html.escape(catalog)}</caption>")
        parts.append(
            "<tr><th>Schema</th><th>Tables</th><th>Columns</th><th>FKs</th>"
            "<th>Views</th><th>ERD</th><th>LinkML</th><th>DDL</th></tr>"
        )
        for row in sorted(by_catalog[catalog], key=lambda r: r["schema"]):
            fk_class = "num warn" if row["foreign_keys"] == 0 else "num"
            parts.append(
                "<tr>"
                f"<td>{html.escape(row['schema'])}</td>"
                f"<td class='num'>{row['tables']}</td>"
                f"<td class='num'>{row['columns']}</td>"
                f"<td class='{fk_class}'>{row['foreign_keys']}</td>"
                f"<td class='num'>{row['views']}</td>"
                f"<td>{_link(out_dir, row['schemaspy'], 'ERD')}</td>"
                f"<td>{_link(out_dir, row['linkml'], 'YAML')}</td>"
                f"<td>{_link(out_dir, row['sql'], 'SQL')}</td>"
                "</tr>"
            )
        parts.append("</table>")

    parts.append(
        "<p class='sub'>Greyed entries have not been generated yet. A zero foreign-key "
        "count is highlighted: the database declares no relationships, so ERDs and "
        "LinkML ranges for it carry no links.</p>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)
