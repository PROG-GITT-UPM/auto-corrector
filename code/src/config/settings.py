"""Service settings, read from environment variables (see `.env.example` at the repository root).

Only infrastructure lives here. Which assignments are graded, their hidden tests and whether pushes
are watched are stored in PostgreSQL and managed through the API, so that the whole state of the
service travels with the database volume.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    pass


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    try:
        return int(value) if value else default
    except ValueError:
        raise ConfigError(f"{name} must be an integer, not {value!r}") from None


@dataclass
class Settings:
    organization: str
    github_token: str
    api_token: str
    database_url: str
    config_repo: str = "classroom50"
    github_api: str = "https://api.github.com"
    github_web: str = "https://github.com"
    work_dir: Path = Path("/var/lib/grader")
    workers: int = 2
    save_every: int = 60          # seconds between score writes to Classroom 50
    watch_tick: int = 15          # seconds between checks of which watched assignments are due
    sandbox: dict = field(default_factory=lambda: {"type": "docker"})

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [v for v in ("GITHUB_ORGANIZATION", "GITHUB_TOKEN", "GRADER_API_TOKEN",
                               "DATABASE_URL") if not os.environ.get(v)]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            organization=os.environ["GITHUB_ORGANIZATION"],
            github_token=os.environ["GITHUB_TOKEN"],
            api_token=os.environ["GRADER_API_TOKEN"],
            database_url=os.environ["DATABASE_URL"],
            config_repo=os.environ.get("CLASSROOM50_CONFIG_REPO", "classroom50"),
            github_api=os.environ.get("GITHUB_API", "https://api.github.com"),
            github_web=os.environ.get("GITHUB_WEB", "https://github.com"),
            work_dir=Path(os.environ.get("GRADER_WORK_DIR", "/var/lib/grader")),
            workers=_int("GRADER_WORKERS", 2),
            save_every=_int("GRADER_SAVE_EVERY", 60),
            watch_tick=_int("GRADER_WATCH_TICK", 15),
            sandbox={
                "type": os.environ.get("SANDBOX_TYPE", "docker"),
                "image": os.environ.get("SANDBOX_IMAGE", "grader-sandbox:3.12"),
                "memory": os.environ.get("SANDBOX_MEMORY", "256m"),
                "cpus": os.environ.get("SANDBOX_CPUS", "1"),
                "processes": _int("SANDBOX_PROCESSES", 128),
                "insecure": os.environ.get("SANDBOX_INSECURE", "").lower() in ("1", "true", "yes"),
            },
        )
