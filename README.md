# lakehouse-schema-extraction

Extract complete DDL and schema metadata — including foreign keys — from databases
federated behind the JGI Starburst lakehouse at `lakehouse-pov.jgi.lbl.gov`, then turn
it into a browsable ERD and a LinkML schema.

## Why this exists

Trino's `information_schema` is a lowest-common-denominator view across every connector
type. An S3/Parquet catalog has no foreign keys, so the abstraction has nowhere to put
them. Querying Trino directly gets you tables, columns, and types — but **no primary
keys, foreign keys, indexes, or check constraints**. Trino's JDBC driver likewise
returns nothing from `getImportedKeys()`, which is exactly the call SchemaSpy makes to
draw relationship lines. Point SchemaSpy at Trino and you get disconnected boxes.

This tool goes around that using each connector's `system.query` table function, which
passes raw SQL through to the underlying database. That reaches the native catalog
(`pg_catalog` for Postgres), which does have the full constraint graph.

```
lakehouse ──extract──> out/*.sql ──load──> local Postgres ─┬─> SchemaSpy ERD
              │                            (docker)        └─> LinkML schema
              └────────> out/*.json  (structured metadata)
```

The local Postgres holds **structure only** — no rows are ever copied out of GOLD. It
exists because both SchemaSpy and `schema-automator` introspect a live database, and
neither can see constraints through Trino.

## Quickstart: GOLD from scratch

Everything below defaults to `gold-db-2_postgresql` / `gold`, so no arguments are needed.

**Prerequisites** — [uv](https://docs.astral.sh/uv/), [just](https://just.systems), and
Docker Desktop running:

```sh
brew install uv just
# Docker Desktop: https://www.docker.com/products/docker-desktop
```

**1. Install** (~30s)

```sh
just install
```

Optionally, copy the sample configuration. Every setting has a working default, so skip
this unless you need to change something:

```sh
cp .env.example .env
```

**2. Authenticate and confirm access** (~1 min)

```sh
just catalogs
```

This is the first command that touches the network, so **a browser window will open**
for OAuth2 SSO. The token is cached in your system keyring afterwards, so nothing later
prompts. Look for `+ gold-db-2_postgresql   postgresql` in the output — the `+` means a
dialect can handle it.

**3. Extract the schema from the lakehouse** (~30s)

```sh
just extract
```

Watch the summary line. You want a non-zero foreign key count:

```
390 relations, 4213 columns, 206 primary keys, 376 foreign keys, 1580 indexes
```

**4. Start a local Postgres and load the structure** (~1 min)

```sh
just db-up
just db-load
```

Expect `loaded: 385 relations, 376 foreign keys, 4 errors`. Those four errors are
unavailable Oracle-migration extensions and are harmless — see below.

**5. Generate the LinkML schema** (~3 min, first run downloads schema-automator)

```sh
just linkml
just linkml-check     # optional: expect "0 errors"
```

**6. Generate the browsable ERD** (~35 min, ~127MB)

```sh
just schemaspy
just browse
```

This one is slow — background it and come back. Everything from step 5 onward is
independent of it.

**When you're done**

```sh
just db-stop      # keep the loaded database for later
just db-down      # or remove it entirely, including the volume
```

**All at once**, if you'd rather not babysit the steps (~40 min, dominated by SchemaSpy):

```sh
just all
```

Run `just` with no arguments at any point to list every recipe.

## Sweeping every Postgres database at once

The quickstart above documents one schema. To do all of them:

```sh
just targets        # preview: what would be processed, and what is skipped and why
just all-postgres   # extract + load + LinkML + index, for every supported schema
```

`all-postgres` takes roughly 20 minutes and covers **24 schemas across 5 catalogs** —
2521 tables and 2364 foreign keys. It deliberately excludes ERDs, which are far slower:

```sh
just schemaspy gold-db-2_postgresql gold    # one ERD, ~35 min for 390 relations
just schemaspy-all                          # every ERD; hours. Background it.
```

Then open `out/index.html` (`just browse`) for a single page linking every schema's
ERD, LinkML file, and DDL, with table and foreign-key counts. Schemas that declare no
foreign keys are highlighted, since their ERDs and LinkML ranges carry no links.

### What gets skipped

`just targets` reports every skip with a reason and never drops anything silently.
As of writing: 24 selected, 26 skipped — Oracle compatibility shims (`dbms_*`, `plv*`,
`plunit`, `utl_file`, `oracle`, `pgagent`), schemas with no tables, and the
`starburst-db_postgresql` infrastructure catalog. Use `just targets --all` to see them
included, and edit the rules in `sweep.py`, which documents why each exists.

### Output layout

```
out/<catalog>/<schema>.sql               DDL
out/<catalog>/<schema>.json              structured metadata
out/<catalog>/<schema>.linkml.yaml       LinkML schema
out/<catalog>/<schema>.load-errors.log   psql errors from loading
out/schemaspy/<catalog>/<schema>/        SchemaSpy site
out/index.html                           index over all of the above
```

The catalog level is required, not cosmetic: `public` exists in five different catalogs
and would otherwise collide. Each catalog also gets its own local database
(`docs_<catalog>`) for the same reason — see `just db-list`.

## Documentation site

A MkDocs site with LinkML reference pages for every schema, published to GitHub Pages.

```sh
just testdoc      # build exactly what CI publishes, locally
just serve        # preview at http://127.0.0.1:8000
```

**Nothing generated is committed.** `docs/`, `mkdocs.yml`, and `site/` are all
gitignored and rebuilt from scratch by CI on every push to `main`. What *is* committed
is `schemas/` — the promoted LinkML files — because the GitHub runner has no lakehouse
access and no Docker database to introspect. That directory is the CI input.

So the loop is: regenerate schemas locally when a database changes, commit them, and
let CI rebuild and publish the site.

```sh
just linkml-all     # regenerate from the lakehouse (needs the local Postgres)
just promote        # copy out/<catalog>/*.linkml.yaml -> schemas/<catalog>/
git add schemas && git commit -m "refresh schemas"
git push            # CI regenerates docs and deploys to gh-pages
```

`.github/workflows/deploy-docs.yml` triggers on pushes to `main` that touch
`schemas/**`, `src/**`, the justfile, or the workflow itself — plus manual
`workflow_dispatch`. It runs `just gendoc` then `just deploy` (`mkdocs gh-deploy`),
publishing to the `gh-pages` branch.

The index page and `mkdocs.yml` navigation are **generated from the schemas**, not
hand-maintained: `lakehouse-build-docs` reads every `schemas/<catalog>/<schema>.linkml.yaml`
and derives the catalog sections, per-schema class/slot/reference counts, and nav tree.
Adding a database means committing its schema file — no config edit. Display names and
descriptions for known catalogs live in `docs_site.py`; unknown ones get a tidied
fallback name automatically.

Reference pages go to `docs/<catalog>/<schema>/`. The catalog level is required:
`public` exists in four catalogs, and a path keyed on schema name alone would have them
overwrite each other. Each schema's directory is cleared before regeneration so a
dropped class cannot leave a stale page behind.

### One local-only caveat

`gen-doc` writes a page per class *and* per slot. A class `Gene` and a slot `gene` want
`Gene.md` and `gene.md` — which a **case-insensitive filesystem (macOS) stores as a
single file**. Locally, a handful of pages per schema are therefore merged, and MkDocs
reports "target not found" for the ones that lost. Measured on this data: 6 pages in
`gcs-vm-1/public`, 2 in `gold`, 1 in `img/public`.

CI runs on Linux, where both files coexist, so **the published site is complete** and
these warnings do not appear there. `just testdoc` prints a reminder on macOS. If you
need a byte-identical local preview, build in a Linux container.

## Install

```sh
just install          # uv sync
```

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems), and Docker for
the documentation steps.

## Configuration

Copy `.env.example` to `.env` to change any default. The file is optional — every value
falls back to a working default — and it is read by **both** `just` (via
`set dotenv-load`) and docker-compose, so the two cannot disagree about, say, which
port the database is on. Shell variables override `.env`:

```sh
POSTGRES_PORT=6000 just db-up
```

`.env` is gitignored; `.env.example` is tracked. Keep real credentials out of the latter.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TARGET_CATALOG` | `gold-db-2_postgresql` | Catalog to extract, as shown by `just catalogs` |
| `TARGET_SCHEMA` | `gold` | Schema inside that catalog |
| `OUT_DIR` | `out` | Where generated files are written |
| `LAKEHOUSE_HOST` / `LAKEHOUSE_PORT` | JGI lakehouse, `443` | Starburst endpoint |
| `TRINO_USER` | your OS username | Recorded against queries in the Insights UI |
| `TRINO_PASSWORD` | unset | See below |
| `POSTGRES_*` | `postgres` / `lakehouse` / `golddocs` / `5433` | The throwaway documentation database |

The `POSTGRES_*` values are not secrets: that container is local, disposable, and holds
only extracted structure — no rows are ever copied out of the source database.

## Authentication

OAuth2 by default — a browser opens on first use and the token is cached in your system
keyring for later runs. Host and port default to the JGI lakehouse; override with
`--host`/`--port` or `LAKEHOUSE_HOST`/`LAKEHOUSE_PORT`.

Setting `TRINO_PASSWORD` switches every run to basic auth. Leave it unset for normal
use: OAuth2 needs no stored credential. If a headless or service account requires it,
prefer exporting it in the shell over writing it into `.env`.

## Use

```sh
just catalogs                              # list catalogs + connector types
just schemas gold-db-2_postgresql          # list schemas in a catalog
just extract                               # defaults to gold-db-2_postgresql.gold
just extract other-db_postgresql public    # any catalog/schema
```

Each extraction writes two files to `out/`:

| File | Contents |
| --- | --- |
| `<catalog>.<schema>.sql` | Loadable DDL: extensions, sequences, tables, constraints, indexes, views, comments |
| `<catalog>.<schema>.json` | The same metadata structured, for diffing or custom tooling |

## Documentation pipeline

```sh
just all      # extract -> db-up -> db-load -> linkml -> schemaspy -> browse
```

Or step by step:

```sh
just db-up         # start the throwaway Postgres (compose, waits for healthy)
just db-load       # load the DDL, report relations / FKs / errors
just linkml        # schema-automator + refinement -> out/gold.linkml.yaml
just linkml-check  # validate with linkml-lint
just schemaspy     # SchemaSpy introspects it      -> out/schemaspy/index.html
just browse        # open the ERD
just db-psql       # psql shell, for poking at the loaded structure
just db-down       # remove container and volume
```

### How LinkML generation works

`just linkml` runs `schemauto import-sql` and then a refinement pass, because
schema-automator gets the hard half right and the easy half wrong:

| | schema-automator | after refinement |
| --- | --- | --- |
| Foreign keys → object ranges | 376 ✅ | 376 (preserved, retargeted) |
| Primary keys → identifiers | 204 ✅ | 204 |
| Scalar types | 3747 of 4213 flattened to `string` ❌ | corrected from `format_type` |
| NOT NULL → `required` | absent ❌ | 276 slots |
| Class naming | raw table names ❌ | UpperCamelCase, table kept as `aliases` |
| `-n` / schema name | ignored upstream ❌ | applied |

Two details worth knowing if you run `schemauto` yourself:

- **`import-sql` has no `--schema` option** and introspects the connection's default
  schema. Against `gold` it silently returns an empty schema. The fix is
  `?options=-csearch_path%3Dgold` in the connection URI.
- **Object ranges are never overwritten** by the refinement pass. An attribute whose
  range names a class is a resolved foreign key, and it is left alone apart from
  following the CamelCase rename.

`just linkml-check` gates on errors only. GOLD produces ~2194 `recommended` warnings,
all "does not have recommended slot 'description'" — the source database has no table
or column comments, so there is nothing to harvest. That is a data gap, not a tool bug.
Use `--db-names` on `lakehouse-refine-linkml` to keep raw table names instead.

`just schemaspy` takes roughly 35 minutes on GOLD (390 relations) and produces a ~127MB
static site. `relationships.real.*.svg` under `out/schemaspy/diagrams/summary/` is the
foreign-key ERD; SchemaSpy also generates `.implied.` variants inferred from naming,
which are guesses rather than declared constraints.

Docker services live in `docker-compose.yaml`. See [Configuration](#configuration) for
the settings they read.

## Load errors are expected, and informative

`just db-load` continues past errors and writes them to `out/load-errors.log`, because a
partial schema is more useful than none and the failures tell you what the extractor
missed. On GOLD the remaining errors are four unavailable extensions — `oracle_fdw`,
`ora_migrator`, `db_migrator` (GOLD was migrated from Oracle) and `vector`. Nothing
extracted actually uses them, so they are harmless. If a *table* or *index* fails, that
is a real gap worth reporting.

## Gotchas this codebase already handles

These are the things that break naive catalog extraction, all covered by tests:

- **Unmappable catalog types.** The connector maps every result column to a Trino type
  before returning rows; `regtype`, `oid`, and `int2vector` have no equivalent and fail
  the *entire query*, not just the column. Cast such columns to `::text`, and aggregate
  array columns like `conkey` with `string_agg` rather than returning them as arrays.
- **Quote nesting.** Native SQL is embedded in a Trino string literal, so every single
  quote must be doubled. `LakehouseClient.passthrough()` does this centrally; callers
  write native SQL normally.
- **Hyphenated catalog names.** `gold-db-2_postgresql` is not a legal bare identifier
  and must be double-quoted everywhere it appears.
- **Reference cycles.** Foreign keys are emitted as `ALTER TABLE ... ADD CONSTRAINT`
  after all tables, so load order never matters. (This is also why the dump cannot be
  loaded into SQLite, which has no `ALTER TABLE ADD CONSTRAINT`.)
- **Sequences before tables.** `serial` columns default to `nextval`, which fails if the
  sequence does not exist yet.
- **Enums and domains before tables.** One missing enum fails every `CREATE TABLE` using
  it, and then each index and foreign key on those tables cascades into "relation does
  not exist". A single missing type in smc-db produced 436 errors and cost 206 of its
  223 foreign keys.
- **`SET search_path` in the dump.** `pg_get_constraintdef` omits the schema for tables
  on the source session's path, so references arrive as `REFERENCES yesnocv(id)`. They
  only resolve on load if the path is set first.
- **Sweeps must not read targets on stdin.** `docker`, `psql`, and `docker compose run`
  all consume stdin, so a `while read` loop fed by a pipe silently processes only its
  first target. The sweep recipes read on fd 3 instead.
- **Extensions before indexes.** GOLD's indexes use `gin_trgm_ops`, which needs `pg_trgm`.
- **Materialized views.** They are absent from `pg_views`, carry no rows in the column
  query, and would silently vanish. They need `CREATE MATERIALIZED VIEW` (there is no
  `OR REPLACE` form), and their indexes must follow them.
- **View-on-view dependencies.** Views are topologically sorted so each is created after
  the views it selects from; cycles degrade to original order rather than raising.

## Adding a database type

Dialects are selected by Trino's `connector_name` (from `system.metadata.catalogs`),
which is authoritative. Catalog naming conventions like the `_postgresql` suffix are
only a fallback hint, since a name is a local convention while the connector name is
what Trino actually loaded.

To add MySQL or another engine:

1. Implement the `Dialect` protocol in `src/lakehouse_schema_extraction/dialects/`
   (`extract`, `render_ddl`, `list_schemas`) — see `postgres.py`.
2. Normalise constraints to the portable shape used by `SchemaMetadata`:
   `constraint_type` of `PRIMARY KEY` / `FOREIGN KEY` / `UNIQUE` / `CHECK`, plus
   `columns` and `target_columns` lists.
3. Register it in `_DIALECTS` in `dialects/__init__.py`.

Nothing else changes — the CLI and output formats are dialect agnostic. Note that
`system.query` pass-through must be enabled for the connector; verify with:

```sql
SELECT * FROM TABLE("<catalog>".system.query(query => 'SELECT version()'));
```

## Layout

```
src/lakehouse_schema_extraction/   library: client, dialects, LinkML refinement
src/scripts/extract_ddl.py         CLI entry point (lakehouse-schema)
src/scripts/refine_linkml.py       CLI entry point (lakehouse-refine-linkml)
tests/                             offline tests over metadata fixtures
docker-compose.yaml                throwaway Postgres + SchemaSpy
.env.example                       sample configuration; copy to .env
justfile                           every workflow above
```

## Development

```sh
just test      # pytest
just lint      # ruff
just check     # both
```

Tests are fully offline: dialects are pure functions over metadata dicts, so DDL
rendering, view ordering, and quote escaping are tested without a lakehouse connection.
