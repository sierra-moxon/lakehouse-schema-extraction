# lakehouse-schema-extraction

catalog := "gold-db-2_postgresql"
schema  := "gold"
out     := "out"

# The throwaway Postgres is defined in docker-compose.yaml and configured by .env;
# these mirror those defaults for commands that run on the host (schema-automator).
pg_port := env("POSTGRES_PORT", "5433")
pg_db   := env("POSTGRES_DB", "golddocs")
pg_user := env("POSTGRES_USER", "postgres")
pg_pass := env("POSTGRES_PASSWORD", "lakehouse")

dump := out / catalog + "." + schema + ".sql"
meta := out / catalog + "." + schema + ".json"

_default:
    @just --list

# --- development ----------------------------------------------------------

# Create the venv and install everything, including dev tools
install:
    uv sync

# Run the test suite
test *args:
    uv run pytest {{args}}

# Lint
lint:
    uv run ruff check src tests

# Auto-fix lint findings and format
fmt:
    uv run ruff check --fix src tests
    uv run ruff format src tests

# Everything CI would run
check: lint test

# --- extraction -----------------------------------------------------------

# Show the CLI help
cli *args:
    uv run lakehouse-schema {{args}}

# List catalogs and their connector types
catalogs:
    uv run lakehouse-schema catalogs

# List schemas in a catalog
schemas catalog=catalog:
    uv run lakehouse-schema schemas {{catalog}}

# Extract DDL + JSON metadata from the lakehouse
extract catalog=catalog schema=schema:
    uv run lakehouse-schema extract {{catalog}} {{schema}} --out-dir {{out}}

# --- local database -------------------------------------------------------

# Start the throwaway Postgres and block until its healthcheck passes
db-up:
    docker compose up -d --wait postgres
    @echo "postgres ready on localhost:{{pg_port}}"

# Errors do not stop the load: a partial schema is still useful, and the failures
# tell you what the extractor missed. They are written to out/load-errors.log.
# Load the extracted DDL into the throwaway database
db-load:
    #!/usr/bin/env bash
    set -uo pipefail
    test -f "{{dump}}" || { echo "no dump at {{dump}} -- run 'just extract' first"; exit 1; }
    docker compose exec -T postgres psql -q -U {{pg_user}} -d {{pg_db}} \
        < "{{dump}}" 2> {{out}}/load-errors.log
    errors=$(grep -c '^ERROR' {{out}}/load-errors.log || true)
    read -r tables fks <<< "$(docker compose exec -T postgres psql -tAX -U {{pg_user}} \
        -d {{pg_db}} -c "SELECT (SELECT count(*) FROM information_schema.tables \
            WHERE table_schema='{{schema}}'), (SELECT count(*) FROM pg_constraint c \
            JOIN pg_namespace n ON n.oid=c.connamespace \
            WHERE n.nspname='{{schema}}' AND c.contype='f')" | tr '|' ' ')"
    echo "loaded: ${tables} relations, ${fks} foreign keys, ${errors} errors"
    [ "${errors}" -gt 0 ] && echo "see {{out}}/load-errors.log"
    exit 0

# Start the database and load the extracted DDL
db: db-up db-load

# Open a psql shell against the throwaway database
db-psql:
    docker compose exec postgres psql -U {{pg_user}} -d {{pg_db}}

# Stop the database, keeping its volume
db-stop:
    docker compose stop postgres

# Remove the database and its volume entirely
db-down:
    docker compose down -v

# --- outputs --------------------------------------------------------------

# schema-automator resolves foreign keys into object ranges but collapses most scalar
# types to string and ignores -n; the refine pass restores types, nullability, and
# naming from the extracted metadata without touching the resolved references.
# search_path in the URI is what points it at a non-public schema -- import-sql has
# no --schema option and otherwise introspects `public` and finds nothing.
# Generate a LinkML schema from the loaded database, then refine it
linkml name=schema:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p {{out}}
    uvx --from schema-automator --with psycopg2-binary schemauto import-sql \
        "postgresql+psycopg2://{{pg_user}}:{{pg_pass}}@localhost:{{pg_port}}/{{pg_db}}?options=-csearch_path%3D{{schema}}" \
        -o {{out}}/{{name}}.linkml.yaml 2> {{out}}/linkml-import.log
    uv run lakehouse-refine-linkml {{out}}/{{name}}.linkml.yaml "{{meta}}" -n {{name}}

# Gates on errors only: the "recommended" warnings are missing descriptions, which
# cannot be fixed when the source database carries no comments.
# Validate the generated schema with linkml-lint
linkml-check name=schema:
    #!/usr/bin/env bash
    set -uo pipefail
    report=$(uvx --from linkml linkml-lint --validate {{out}}/{{name}}.linkml.yaml 2>&1 || true)
    errors=$(printf '%s\n' "$report" | grep -cE '^[[:space:]]+error[[:space:]]' || true)
    warnings=$(printf '%s\n' "$report" | grep -cE '^[[:space:]]+warning[[:space:]]' || true)
    echo "linkml-lint: ${errors} errors, ${warnings} warnings"
    printf '%s\n' "$report" | grep -E '^[[:space:]]+error[[:space:]]' | head -20
    printf '%s\n' "$report" | grep -oE '\([a-z_]+\)$' | sort | uniq -c | sort -rn
    [ "${errors}" -eq 0 ]

# Generate a browsable SchemaSpy site from the loaded database
schemaspy:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p {{out}}/schemaspy
    docker compose --profile tools run --rm schemaspy
    echo "wrote {{out}}/schemaspy/index.html"

# Open the generated SchemaSpy site
browse:
    open {{out}}/schemaspy/index.html

# Full pipeline: lakehouse -> DDL -> local postgres -> LinkML + ERD
all: extract db-up db-load linkml schemaspy browse

clean:
    rm -rf {{out}} .pytest_cache .ruff_cache
    find . -name __pycache__ -type d -exec rm -rf {} +
