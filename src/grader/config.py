"""Grader configuration (`grader.toml`). See `grader.example.toml`."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class ConfigError(ValueError):
    pass


@dataclass
class Assignment:
    """A Classroom 50 assignment graded by this grader."""
    classroom: str
    slug: str
    template: str                        # "org/repo" of the assignment: the tests come from here
    ref: str = "main"
    grader_dir: str = ".grader"
    publish_release: bool = True         # Release with the result in the student's repository
    save_score: bool = True              # score in the Classroom 50 scores.json
    extra_tests: Optional[Path] = None   # tests the student does not see (local file)

    @property
    def key(self) -> str:
        return f"{self.classroom}/{self.slug}"


@dataclass
class Config:
    organization: str
    token: str
    config_repo: str = "classroom50"
    api: str = "https://api.github.com"
    web: str = "https://github.com"
    sandbox: dict = field(default_factory=lambda: {"type": "docker"})
    work_dir: Path = Path("/var/lib/grader")
    service: dict = field(default_factory=dict)
    assignments: dict = field(default_factory=dict)

    def assignment(self, classroom: str, slug: str) -> Assignment:
        try:
            return self.assignments[f"{classroom}/{slug}"]
        except KeyError:
            raise ConfigError(f"assignment {classroom}/{slug} is not in the configuration "
                              f"(available: {', '.join(self.assignments) or 'none'})") from None


def load(path: Path, require_token: bool = True) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None

    gh = data.get("github", {})
    if "organization" not in gh:
        raise ConfigError("[github] organization is missing")
    variable = gh.get("token_env", "GITHUB_TOKEN")
    token = os.environ.get(variable, "")
    if require_token and not token:
        raise ConfigError(f"the GitHub token is missing from the environment variable {variable}")

    assignments: dict = {}
    for key, a in data.get("assignments", {}).items():
        if key.count("/") != 1:
            raise ConfigError(f'[assignments."{key}"]: the key must be "classroom/assignment"')
        classroom, slug = key.split("/")
        if "template" not in a:
            raise ConfigError(f'[assignments."{key}"]: template is missing ("org/repo")')
        extra = a.get("extra_tests")
        assignments[key] = Assignment(
            classroom=classroom, slug=slug, template=a["template"], ref=a.get("ref", "main"),
            grader_dir=a.get("grader_dir", ".grader"),
            publish_release=a.get("publish_release", True),
            save_score=a.get("save_score", True),
            extra_tests=(path.parent / extra).resolve() if extra else None,
        )

    return Config(
        organization=gh["organization"], token=token,
        config_repo=gh.get("config_repo", "classroom50"),
        api=gh.get("api", "https://api.github.com"), web=gh.get("web", "https://github.com"),
        sandbox=data.get("sandbox", {"type": "docker"}),
        work_dir=Path(data.get("work", {}).get("dir", "/var/lib/grader")),
        service=data.get("service", {}),
        assignments=assignments,
    )
