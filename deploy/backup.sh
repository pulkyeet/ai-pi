#!/usr/bin/env bash
# Nightly Supabase backup -> R2 object storage (Phase 15).
#
# Runs from GitHub Actions (.github/workflows/keepalive.yml), never from a Fly
# machine — Fly filesystems are ephemeral, so a dump written there is a dump
# that disappears. Supabase's free tier has no managed backups, so this IS the
# backup; restore is documented in docs/runbook.md and exercised monthly.
#
# Requires: pg_dump (postgresql-client), gzip, aws cli v2 (preinstalled on
# GitHub's ubuntu-latest runners). Uploads use the S3 API against the R2
# endpoint; AWS_DEFAULT_REGION=auto is the R2 convention.
set -euo pipefail

: "${SUPABASE_DB_URL:?SUPABASE_DB_URL required (libpq form, ?sslmode=require)}"
: "${R2_ENDPOINT:?R2_ENDPOINT required (S3-compatible URL)}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID required}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY required}"
: "${R2_BUCKET:?R2_BUCKET required}"

# The aws cli reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY; map the R2-prefixed
# names the workflow passes so this script is self-contained.
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
OBJECT="ai-pi-postgres-${STAMP}.sql.gz"
TMPDIR_MINE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_MINE"' EXIT

# Plain-SQL pg_dump, gzipped. `--no-owner`/`--no-privileges` keep the dump
# restore-able into a scratch database by any role. Excludes nothing — this is
# the whole database, schema included, so a restore needs no migration step.
pg_dump --no-owner --no-privileges --dbname="$SUPABASE_DB_URL" \
  | gzip -9 > "$TMPDIR_MINE/$OBJECT"

aws s3 cp "$TMPDIR_MINE/$OBJECT" "s3://$R2_BUCKET/$OBJECT" --endpoint-url "$R2_ENDPOINT"

# Prune old backups, keeping the 30 most recent (a month of nightly dumps).
mapfile -t ALL < <(aws s3 ls "s3://$R2_BUCKET/" --endpoint-url "$R2_ENDPOINT" \
  | awk '{print $4}' | grep -E '^ai-pi-postgres-.*\.sql\.gz$' | sort)
KEEP=${#ALL[@]}
if [ "$KEEP" -gt 30 ]; then
  for ((i = 0; i < KEEP - 30; i++)); do
    aws s3 rm "s3://$R2_BUCKET/${ALL[$i]}" --endpoint-url "$R2_ENDPOINT"
  done
fi

echo "backup uploaded: s3://$R2_BUCKET/$OBJECT"
