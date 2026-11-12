# Shared by the scripts in this folder: run from the repository root and read .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# env_value NAME DEFAULT: the value of NAME in .env (the last one wins), or DEFAULT.
# Reads the file instead of sourcing it, so values are never executed as shell code.
env_value() {
    local value=""
    if [[ -f .env ]]; then
        value="$(grep -E "^[[:space:]]*$1=" .env | tail -n 1 | cut -d= -f2- || true)"
        value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    fi
    echo "${value:-$2}"
}

# Absolute path of a folder from .env (relative paths are relative to the repository root).
env_dir() {
    local dir
    dir="$(env_value "$1" "$2")"
    [[ "$dir" = /* ]] || dir="$ROOT/${dir#./}"
    echo "$dir"
}

compose() { docker compose "$@"; }

wait_for_postgres() {
    for _ in $(seq 1 60); do
        if compose exec -T postgres pg_isready -U grader -d grader >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "PostgreSQL did not become ready" >&2
    return 1
}

# Grading containers left by a grader stopped half-way. Compose does not manage them, so
# `docker compose down` leaves them running; the grader removes them on start, these scripts on stop.
remove_sandbox_containers() {
    local ids
    ids="$(docker ps --all --quiet --filter label=grader.sandbox=1)"
    if [[ -n "$ids" ]]; then
        # shellcheck disable=SC2086
        docker rm --force --volumes $ids >/dev/null
        echo "Removed $(wc -w <<<"$ids") leftover grading container(s)."
    fi
}

# Runs a command as root (sudo when not already root): the data folders belong to the
# containers' users (PostgreSQL uid 999, pgAdmin uid 5050).
as_root() {
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}
