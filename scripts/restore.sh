#!/usr/bin/env bash
# Restores a dump made by backup.sh, replacing the current state of the service.
#
#   scripts/restore.sh data/backups/grader-20261110-030000Z.dump
#
# Stops the grader, restores the database and starts the grader again, which resumes from the
# restored state (queued jobs, pending scores, watch settings...).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

file="${1:-}"
if [[ -z "$file" || ! -f "$file" ]]; then
    echo "Usage: scripts/restore.sh <dump made by backup.sh>" >&2
    exit 2
fi
if [[ "${2:-}" != "--yes" ]]; then
    read -r -p "This replaces the current state of the service with $file. Continue? [y/N] " answer
    [[ "$answer" =~ ^[yY]$ ]] || { echo "Cancelled."; exit 1; }
fi

compose stop grader
compose up -d postgres
wait_for_postgres
compose exec -T postgres pg_restore -U grader -d grader --clean --if-exists --no-owner \
    --single-transaction < "$file"
compose up -d
echo "Restored from $file"
