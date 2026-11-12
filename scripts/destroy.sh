#!/usr/bin/env bash
# Removes the service from this machine: containers, networks, its two images (the grader and
# the sandbox) and the data folders (PostgreSQL and pgAdmin).
#
#   scripts/destroy.sh               # final backup first; keeps BACKUP_DIR and .env
#   scripts/destroy.sh --no-backup   # no final backup; still keeps BACKUP_DIR and .env
#   scripts/destroy.sh --purge       # removes everything, backups and .env included
#   ... --yes                        # do not ask for confirmation (for automation)
#
# Nothing in GitHub is touched: the scores already in Classroom 50 and the Releases in the
# student repositories stay. Images shared with other projects (postgres, the socket proxy,
# pgAdmin) are not removed either.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

backup=true purge=false yes=false
for arg in "$@"; do
    case "$arg" in
        --no-backup) backup=false ;;
        --purge) purge=true; backup=false ;;
        --yes) yes=true ;;
        *) echo "Usage: scripts/destroy.sh [--no-backup | --purge] [--yes]" >&2; exit 2 ;;
    esac
done

postgres_dir="$(env_dir POSTGRES_DATA_DIR ./data/postgres)"
pgadmin_dir="$(env_dir PGADMIN_DATA_DIR ./data/pgadmin)"
backup_dir="$(env_dir BACKUP_DIR ./data/backups)"
remove_dirs=("$postgres_dir" "$pgadmin_dir")
$purge && remove_dirs+=("$backup_dir")

# Never remove the repository, the filesystem root or the home folder by mistake.
for dir in "${remove_dirs[@]}"; do
    case "$(realpath -m "$dir")" in
        /|"$ROOT"|"$HOME"|/home|/var|/var/lib|/srv|/opt|/etc)
            echo "Refusing to remove $dir: check the folders in .env." >&2; exit 1 ;;
    esac
done

echo "This removes the auto-grader service from this machine:"
echo "  - its containers, networks and images (grader and sandbox)"
for dir in "${remove_dirs[@]}"; do echo "  - $dir"; done
$purge && echo "  - $ROOT/.env (the secrets)"
$backup && echo "A final backup is written to $backup_dir first."
$purge || echo "Kept: $backup_dir and .env."
if ! $yes; then
    read -r -p "Type 'delete' to continue: " answer
    [[ "$answer" == "delete" ]] || { echo "Cancelled."; exit 1; }
fi

if $backup; then
    if [[ -d "$postgres_dir" ]] && [[ -n "$(ls -A "$postgres_dir" 2>/dev/null || as_root ls -A "$postgres_dir")" ]]; then
        compose up -d postgres
        wait_for_postgres
        "$ROOT/scripts/backup.sh"
    else
        echo "No database to back up."
    fi
fi

compose stop grader 2>/dev/null || true
compose --profile admin down --remove-orphans
remove_sandbox_containers
docker image rm --force ghcr.io/prog-gitt-upm/auto-grader:latest grader-sandbox:3.12 \
    >/dev/null 2>&1 || true

for dir in "${remove_dirs[@]}"; do
    if [[ -e "$dir" ]]; then
        as_root rm -rf -- "$dir"
        echo "Removed $dir"
    fi
done
if $purge && [[ -f .env ]]; then
    rm -f -- .env
    echo "Removed .env"
fi

echo "The service has been removed."
$purge || echo "Backups and .env are still in $backup_dir and $ROOT/.env."
echo "To install it again: scripts/setup.sh && docker compose up -d --build"
