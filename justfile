# lakehouse-schema-extraction

# Load .env so recipes and docker-compose read the same settings. Without this, just
# would use the fallbacks below while compose used .env, and the two could disagree
# about the port. Missing .env is fine -- every value has a default.
set dotenv-load := true

catalog := env("TARGET_CATALOG", "gold-db-2_postgresql")
schema  := env("TARGET_SCHEMA", "gold")
out     := env("OUT_DIR", "out")

# The throwaway Postgres is defined in docker-compose.yaml and configured by .env;
# these mirror those defaults for commands that run on the host (schema-automator).
pg_port := env("POSTGRES_PORT", "5433")
pg_db   := env("POSTGRES_DB", "golddocs")
pg_user := env("POSTGRES_USER", "postgres")
pg_pass := env("POSTGRES_PASSWORD", "lakehouse")

# Artifacts are namespaced as out/<catalog>/<schema>.{sql,json,linkml.yaml} and
# out/schemaspy/<catalog>/<schema>/. The catalog level is required, not cosmetic:
# `public` exists in six different catalogs and would otherwise collide.

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

# --- discovery ------------------------------------------------------------

# Show the CLI help
cli *args:
    uv run lakehouse-schema {{args}}

# List catalogs and their connector types
catalogs:
    uv run lakehouse-schema catalogs

# List schemas in a catalog
schemas cat=catalog:
    uv run lakehouse-schema schemas {{cat}}

# Show every (catalog, schema) a sweep would process, with skips explained
targets *args:
    uv run lakehouse-schema targets --format table {{args}}

# --- extraction -----------------------------------------------------------

# Extract DDL + JSON metadata for one catalog/schema
extract cat=catalog sch=schema:
    uv run lakehouse-schema extract {{cat}} {{sch}} --out-dir {{out}}/{{cat}} --prefix {{sch}}

# Extract every supported catalog/schema in the lakehouse
extract-all:
    #!/usr/bin/env bash
    set -uo pipefail
    # Targets are read on fd 3, not stdin: docker and psql inside these loops read
    # stdin themselves and would otherwise swallow the remaining lines, silently
    # processing only the first target.
    targets=$(uv run lakehouse-schema targets)
    while IFS=$'\t' read -r cat sch <&3; do
        [ -n "$cat" ] || continue
        echo "=== ${cat}.${sch}"
        just extract "$cat" "$sch" </dev/null || echo "FAILED ${cat}.${sch}"
    done 3<<< "$targets"
    exit 0

# --- local database -------------------------------------------------------

# Start the throwaway Postgres and block until its healthcheck passes
db-up:
    docker compose up -d --wait postgres
    @echo "postgres ready on localhost:{{pg_port}}"

# Errors do not stop the load: a partial schema is still useful, and the failures
# tell you what the extractor missed. They land in out/<catalog>/<schema>.load-errors.log
# Load one extracted schema into a per-catalog database
db-load cat=catalog sch=schema:
    #!/usr/bin/env bash
    set -uo pipefail
    dump="{{out}}/{{cat}}/{{sch}}.sql"
    test -f "$dump" || { echo "no dump at $dump -- run 'just extract {{cat}} {{sch}}'"; exit 1; }
    db="docs_$(echo '{{cat}}' | tr -cs '[:alnum:]' '_' | tr '[:upper:]' '[:lower:]' | sed 's/_*$//')"
    # One database per catalog keeps identically-named schemas (`public` appears in
    # six catalogs) from overwriting each other.
    exists=$(docker compose exec -T postgres psql -tAX -U {{pg_user}} -d postgres \
        -c "SELECT 1 FROM pg_database WHERE datname='${db}'")
    if [ -z "$exists" ]; then
        docker compose exec -T postgres createdb -U {{pg_user}} "$db"
        echo "created database ${db}"
    fi
    # Drop first so reloads are idempotent. Safe: this database is throwaway
    # documentation scaffolding, never a source of record.
    docker compose exec -T postgres psql -q -U {{pg_user}} -d "$db" \
        -c 'DROP SCHEMA IF EXISTS {{sch}} CASCADE' </dev/null >/dev/null 2>&1
    log="{{out}}/{{cat}}/{{sch}}.load-errors.log"
    docker compose exec -T postgres psql -q -U {{pg_user}} -d "$db" < "$dump" 2> "$log"
    errors=$(grep -c '^ERROR' "$log" || true)
    read -r tables fks <<< "$(docker compose exec -T postgres psql -tAX -U {{pg_user}} \
        -d "$db" -c "SELECT (SELECT count(*) FROM information_schema.tables \
            WHERE table_schema='{{sch}}'), (SELECT count(*) FROM pg_constraint c \
            JOIN pg_namespace n ON n.oid=c.connamespace \
            WHERE n.nspname='{{sch}}' AND c.contype='f')" | tr '|' ' ')"
    echo "loaded {{cat}}.{{sch}} into ${db}: ${tables} relations, ${fks} foreign keys, ${errors} errors"
    exit 0

# Load every extracted schema
db-load-all:
    #!/usr/bin/env bash
    set -uo pipefail
    targets=$(uv run lakehouse-schema targets)
    while IFS=$'\t' read -r cat sch <&3; do
        [ -n "$cat" ] || continue
        just db-load "$cat" "$sch" </dev/null
    done 3<<< "$targets"
    exit 0

# Open a psql shell against a catalog's database
db-psql cat=catalog:
    #!/usr/bin/env bash
    db="docs_$(echo '{{cat}}' | tr -cs '[:alnum:]' '_' | tr '[:upper:]' '[:lower:]' | sed 's/_*$//')"
    docker compose exec -it postgres psql -U {{pg_user}} -d "$db"

# List the loaded documentation databases
db-list:
    docker compose exec -T postgres psql -U {{pg_user}} -d postgres \
        -c "SELECT datname FROM pg_database WHERE datname LIKE 'docs\_%' ORDER BY 1"

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
# Generate a LinkML schema for one catalog/schema, then refine it
linkml cat=catalog sch=schema:
    #!/usr/bin/env bash
    set -euo pipefail
    db="docs_$(echo '{{cat}}' | tr -cs '[:alnum:]' '_' | tr '[:upper:]' '[:lower:]' | sed 's/_*$//')"
    dir="{{out}}/{{cat}}"
    mkdir -p "$dir"
    uvx --from schema-automator --with psycopg2-binary schemauto import-sql \
        "postgresql+psycopg2://{{pg_user}}:{{pg_pass}}@localhost:{{pg_port}}/${db}?options=-csearch_path%3D{{sch}}" \
        -o "$dir/{{sch}}.linkml.yaml" 2> "$dir/{{sch}}.linkml-import.log"
    uv run lakehouse-refine-linkml "$dir/{{sch}}.linkml.yaml" "$dir/{{sch}}.json" -n {{sch}}

# Generate LinkML schemas for everything extracted and loaded
linkml-all:
    #!/usr/bin/env bash
    set -uo pipefail
    targets=$(uv run lakehouse-schema targets)
    while IFS=$'\t' read -r cat sch <&3; do
        [ -n "$cat" ] || continue
        echo "=== ${cat}.${sch}"
        just linkml "$cat" "$sch" </dev/null || echo "FAILED ${cat}.${sch}"
    done 3<<< "$targets"
    exit 0

# Gates on errors only: the "recommended" warnings are missing descriptions, which
# cannot be fixed when the source database carries no comments.
# Validate a generated schema with linkml-lint
linkml-check cat=catalog sch=schema:
    #!/usr/bin/env bash
    set -uo pipefail
    file="{{out}}/{{cat}}/{{sch}}.linkml.yaml"
    report=$(uvx --from linkml linkml-lint --validate "$file" 2>&1 || true)
    errors=$(printf '%s\n' "$report" | grep -cE '^[[:space:]]+error[[:space:]]' || true)
    warnings=$(printf '%s\n' "$report" | grep -cE '^[[:space:]]+warning[[:space:]]' || true)
    echo "linkml-lint {{cat}}.{{sch}}: ${errors} errors, ${warnings} warnings"
    printf '%s\n' "$report" | grep -E '^[[:space:]]+error[[:space:]]' | head -20
    [ "${errors}" -eq 0 ]

# SchemaSpy is slow -- roughly 35 minutes for GOLD's 390 relations. Run it per schema,
# or use schemaspy-all when you can spare the hours.
# Generate a browsable ERD for one catalog/schema
schemaspy cat=catalog sch=schema:
    #!/usr/bin/env bash
    set -euo pipefail
    db="docs_$(echo '{{cat}}' | tr -cs '[:alnum:]' '_' | tr '[:upper:]' '[:lower:]' | sed 's/_*$//')"
    dest="$PWD/{{out}}/schemaspy/{{cat}}/{{sch}}"
    mkdir -p "$dest"
    # The image entrypoint already supplies -o /output, so passing our own -o fails.
    # Per-schema output comes from mounting a different host directory there instead.
    docker compose --profile tools run --rm -v "$dest:/output" schemaspy \
        -t pgsql11 -host postgres -port 5432 -db "$db" \
        -u {{pg_user}} -p {{pg_pass}} -s {{sch}} -imageformat svg -vizjs
    echo "wrote {{out}}/schemaspy/{{cat}}/{{sch}}/index.html"

# Generate ERDs for every target. Hours of work -- background it.
schemaspy-all:
    #!/usr/bin/env bash
    set -uo pipefail
    targets=$(uv run lakehouse-schema targets)
    while IFS=$'\t' read -r cat sch <&3; do
        [ -n "$cat" ] || continue
        echo "=== ${cat}.${sch}"
        just schemaspy "$cat" "$sch" </dev/null || echo "FAILED ${cat}.${sch}"
    done 3<<< "$targets"
    just index
    exit 0

# Build out/index.html linking every extracted schema, LinkML file and ERD
index:
    uv run lakehouse-build-index --out-dir {{out}}

# Open the index page
browse:
    open {{out}}/index.html

# --- documentation site ---------------------------------------------------

# out/ is generated and gitignored; schemas/ is the tracked, publishable copy that
# the docs site and CI build from.
# Promote generated LinkML schemas into the tracked schemas/ directory
promote:
    #!/usr/bin/env bash
    set -euo pipefail
    count=0
    for f in {{out}}/*/*.linkml.yaml; do
        [ -e "$f" ] || { echo "no schemas in {{out}} -- run 'just linkml-all' first"; exit 1; }
        cat_dir=$(basename "$(dirname "$f")")
        mkdir -p "schemas/$cat_dir"
        cp "$f" "schemas/$cat_dir/"
        count=$((count + 1))
    done
    echo "promoted ${count} schemas into schemas/"

# Generate LinkML reference pages for every promoted schema
gendoc:
    #!/usr/bin/env bash
    set -uo pipefail
    for f in schemas/*/*.linkml.yaml; do
        [ -e "$f" ] || { echo "nothing in schemas/ -- run 'just promote'"; exit 1; }
        cat_dir=$(basename "$(dirname "$f")")
        name=$(basename "$f" .linkml.yaml)
        echo "=== ${cat_dir}/${name}"
        # Output is per catalog AND schema: `public` exists in four catalogs and would
        # otherwise collide. Clearing first keeps a regenerated schema from mixing new
        # pages with pages for classes that no longer exist.
        rm -rf "docs/${cat_dir}/${name}"
        mkdir -p "docs/${cat_dir}/${name}"
        uvx --from linkml gen-doc -d "docs/${cat_dir}/${name}" "$f" </dev/null \
            || echo "FAILED ${cat_dir}/${name}"
    done
    just docs-index

# Rebuild docs/index.md and the mkdocs nav from schemas/
docs-index:
    uv run lakehouse-build-docs

# Build the static site into site/
site:
    uv run --group docs mkdocs build

# Build exactly what CI publishes, from the committed schemas/ directory.
# Nothing here is committed: docs/, mkdocs.yml and site/ are all gitignored.
# Generate and build the whole docs site locally, as CI does
testdoc: gendoc site
    #!/usr/bin/env bash
    echo ""
    echo "site built in site/ -- preview it with 'just serve'"
    # gen-doc writes a page per class AND per slot. A class named Gene and a slot named
    # gene want Gene.md and gene.md, which a case-insensitive filesystem stores as one
    # file. mkdocs then reports the other as a broken link. CI runs on Linux, where both
    # exist, so these warnings are local-only and the published site is complete.
    if [ "$(uname)" = "Darwin" ]; then
        echo ""
        echo "note: macOS has a case-insensitive filesystem, so a few class/slot pages"
        echo "      whose names differ only by case are merged locally. Expect some"
        echo "      'target not found' warnings above; the Linux CI build is unaffected."
    fi

# Serve the documentation site locally at http://127.0.0.1:8000
serve:
    uv run --group docs mkdocs serve

# Deliberately not `mkdocs gh-deploy`: that commits ~576MB of built HTML to a branch,
# which every clone would then carry. CI uploads the site as a Pages artifact instead,
# so no generated HTML is ever stored in git.
# Trigger the CI workflow that builds and publishes the site
deploy:
    gh workflow run deploy-docs.yml
    @echo "watch it with: gh run watch"

# Refresh schemas/ from a fresh extraction, then rebuild the site
docs: promote testdoc

# Full sweep except ERDs: extract, load, LinkML, index. Minutes, not hours.
all-postgres: extract-all db-up db-load-all linkml-all index browse

# Single-target pipeline for the default catalog/schema
all: extract db-up db-load linkml index browse

clean:
    rm -rf {{out}} .pytest_cache .ruff_cache
    find . -name __pycache__ -type d -exec rm -rf {} +
