#!/usr/bin/env bash
# Dumps the whole state of the service (the PostgreSQL database) into BACKUP_DIR and keeps the
# newest BACKUP_KEEP dumps. Safe to run while the service is working.
#
#   scripts/backup.sh               # e.g. from cron: 0 3 * * * /path/to/auto-grader/scripts/backup.sh
#   scripts/backup.sh --with-env    # also copies .env (the secrets) next to the dump
#
# The dump does not include .env. To move the service to another machine you need both.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

with_env=false
[[ "${1:-}" == "--with-env" ]] && with_env=true

backup_dir="$(env_dir BACKUP_DIR ./data/backups)"
keep="$(env_value BACKUP_KEEP 14)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

stamp="$(date -u +%Y%m%d-%H%M%SZ)"
# Two backups in the same second (e.g. stop.sh --backup then destroy.sh) must not overwrite.
n=1
while [[ -e "$backup_dir/grader-$stamp.dump" ]]; do
    stamp="$(date -u +%Y%m%d-%H%M%SZ)-$((n++))"
done
file="$backup_dir/grader-$stamp.dump"
# Custom format: compressed, and restorable with pg_restore --clean onto an existing database.
compose exec -T postgres pg_dump -U grader -d grader --format=custom > "$file.partial"
mv "$file.partial" "$file"
chmod 600 "$file"
echo "Backup written: $file ($(du -h "$file" | cut -f1))"

if $with_env; then
    install -m 600 .env "$backup_dir/env-$stamp"
    echo "Secrets copied: $backup_dir/env-$stamp (keep it somewhere safe)"
fi

# Keep the newest $keep dumps (and their .env copies).
ls -1t "$backup_dir"/grader-*.dump 2>/dev/null | tail -n +"$((keep + 1))" | while read -r old; do
    rm -f -- "$old" "$backup_dir/env-$(basename "$old" .dump | sed 's/^grader-//')"
    echo "Removed old backup: $old"
done
