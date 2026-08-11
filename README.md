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

## Install

```sh
just install          # uv sync
```

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems), and Docker for
the documentation steps.

## Authentication

OAuth2 by default — a browser opens on first use and the token is cached in your system
keyring for later runs. Set `TRINO_PASSWORD` (and optionally `TRINO_USER`) for basic
auth instead. Host and port default to the JGI lakehouse; override with `--host`/`--port`
or `LAKEHOUSE_HOST`/`LAKEHOUSE_PORT`.

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

Docker services live in `docker-compose.yaml` and are configured by `.env`. The
credentials there are not secrets — the database is local, disposable, and structure-only.

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
src/lakehouse_schema_extraction/   library: client, dialects
src/scripts/extract_ddl.py         CLI entry point (lakehouse-schema)
tests/                             offline tests over metadata fixtures
docker-compose.yaml / .env         throwaway Postgres + SchemaSpy
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
