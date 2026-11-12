#!/usr/bin/env bash
# Stops the service. Nothing is lost: all the state stays in POSTGRES_DATA_DIR and the service
# resumes where it was with `docker compose up -d`.
#
#   scripts/stop.sh             # stop
#   scripts/stop.sh --backup    # make a backup first (scripts/backup.sh)
#
# The grader gets up to 60 s (stop_grace_period) to finish and write the pending scores to
# Classroom 50. Jobs still half-way are queued again on the next start.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ "${1:-}" == "--backup" ]]; then
    if compose ps --status running --services 2>/dev/null | grep -qx postgres; then
        "$ROOT/scripts/backup.sh"
    else
        echo "PostgreSQL is not running: no backup made." >&2
    fi
elif [[ -n "${1:-}" ]]; then
    echo "Usage: scripts/stop.sh [--backup]" >&2
    exit 2
fi

# The grader first, while the database is still up, so it can save the pending scores.
compose stop grader
compose down
remove_sandbox_containers
echo "Service stopped. Start it again with: docker compose up -d"
