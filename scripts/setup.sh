#!/usr/bin/env bash
# First-time preparation on a machine: creates .env from the example (if missing) and the host
# folders configured in it, with the owners the containers need.
#
#   scripts/setup.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
    echo "Created .env from .env.example: fill in the tokens and passwords before starting."
fi

postgres_dir="$(env_dir POSTGRES_DATA_DIR ./data/postgres)"
pgadmin_dir="$(env_dir PGADMIN_DATA_DIR ./data/pgadmin)"
backup_dir="$(env_dir BACKUP_DIR ./data/backups)"

mkdir -p "$postgres_dir" "$pgadmin_dir" "$backup_dir"
chmod 700 "$backup_dir"   # dumps hold everything, hidden tests included
# The pgAdmin image runs as uid 5050 and cannot write a folder owned by someone else.
if [[ "$(stat -c %u "$pgadmin_dir")" != "5050" ]]; then
    if [[ $EUID -eq 0 ]]; then chown 5050:5050 "$pgadmin_dir"
    else sudo chown 5050:5050 "$pgadmin_dir"; fi
fi
# The PostgreSQL image takes ownership of its data folder by itself on the first start.

echo "Data folders ready:"
echo "  PostgreSQL: $postgres_dir"
echo "  pgAdmin:    $pgadmin_dir"
echo "  Backups:    $backup_dir"
echo "Next: docker compose up -d --build"
