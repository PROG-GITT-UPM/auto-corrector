"""What gets graded: finding the repositories of an assignment, choosing the commit and preparing
the folder.

Classroom 50 creates one repository per student named `<classroom>-<assignment>-<user>`. With the
"Do not use the built-in autograder" option (`no_autograder`) the repository is an exact copy of
the template, without workflows: pushes trigger nothing in GitHub Actions.

The folder that gets graded is the student's code with the `.grader/` folder (tests and engine)
and the protected files ALWAYS copied from the assignment template. That way it does not matter
whether the student has touched their tests or their data files.
"""
from __future__ import annotations

import fnmatch
import io
import json
import logging
import shutil
import tarfile
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Assignment, Config
from .github import GitHub, GitHubError

log = logging.getLogger(__name__)


@dataclass
class Submission:
    organization: str
    classroom: str
    assignment: str
    user: str
    repo: str
    sha: str
    date: str                # commit date (committer date), UTC, ISO 8601 with Z
    branch: str
    web: str = "https://github.com"

    @property
    def sha7(self) -> str:
        return self.sha[:7]

    @property
    def repo_url(self) -> str:
        return f"{self.web}/{self.organization}/{self.repo}"

    @property
    def commit_url(self) -> str:
        return f"{self.repo_url}/commit/{self.sha}"

    @property
    def review_url(self) -> str:
        # The `feedback` branch is frozen at the initial commit (Classroom 50 opens it with the
        # "Feedback" pull request): the comparison shows everything the student has done.
        return f"{self.repo_url}/compare/feedback...{self.sha}"


@dataclass
class Template:
    repo: str
    sha: str
    dir: Path                # extracted template
    settings: dict           # contents of .grader/assignment.toml

    @property
    def sha7(self) -> str:
        return self.sha[:7]


def iso_date(text: str) -> str:
    """Normalizes a GitHub date to "YYYY-MM-DDTHH:MM:SSZ" in UTC."""
    d = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_tarball(data: bytes, dest: Path) -> None:
    """Extracts the GitHub tarball, dropping the root folder ("org-repo-sha/").

    Uses the tarfile `data` filter: it rejects absolute paths, `..`, links that leave the folder,
    devices and special permissions. A student cannot write outside `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = []
        for m in tar.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            m.name = parts[1]
            if m.islnk() or m.issym():
                continue  # links are not needed for grading and are a way out
            members.append(m)
        tar.extractall(dest, members=members, filter="data")


class Repositories:
    """GitHub access for one configuration: Classroom 50 assignments, students and templates."""

    def __init__(self, gh: GitHub, cfg: Config, template_ttl: float = 300.0) -> None:
        self.gh = gh
        self.cfg = cfg
        self.template_ttl = template_ttl
        self._templates: dict = {}
        self._lock = threading.Lock()

    # ─── Classroom 50 configuration ──────────────────────────────────────────

    def classroom_assignments(self, classroom: str) -> list:
        text, _ = self.gh.read_file(self.cfg.organization, self.cfg.config_repo,
                                    f"{classroom}/assignments.json")
        data = json.loads(text)
        return data.get("assignments", []) if isinstance(data, dict) else data

    def classroom_assignment(self, classroom: str, slug: str) -> dict:
        for a in self.classroom_assignments(classroom):
            if a.get("slug") == slug:
                return a
        raise GitHubError(404, f"assignment {slug} is not in {classroom}/assignments.json")

    # ─── Students ────────────────────────────────────────────────────────────

    def list_repos(self, assignment: Assignment) -> list:
        """[(user, repo name, pushed_at)] for the assignment, most recent push first.

        A single paginated query to the organization (100 repos per page) covers every student:
        there is no need for one query per student to find out who has pushed."""
        prefix = f"{assignment.classroom}-{assignment.slug}-"
        # Other assignments whose name starts the same way ("lab-02" and "lab-02-b"): their repos
        # do not belong to this one.
        others = [f"{assignment.classroom}-{a['slug']}-"
                  for a in self.classroom_assignments(assignment.classroom)
                  if a.get("slug", "") != assignment.slug
                  and f"{assignment.classroom}-{a.get('slug', '')}-".startswith(prefix)]
        result = []
        for repo in self.gh.paginate(f"/orgs/{self.cfg.organization}/repos", type="all",
                                     sort="pushed", direction="desc"):
            name = repo["name"]
            if not name.startswith(prefix) or any(name.startswith(o) for o in others):
                continue
            user = name[len(prefix):]
            if user:
                result.append((user, name, repo.get("pushed_at") or ""))
        return result

    def resolve(self, assignment: Assignment, user: str, until: Optional[str] = None) -> Submission:
        """A student's submission: the latest commit on the default branch (or the latest one
        before `until`, to grade what was submitted on time)."""
        org = self.cfg.organization
        repo = f"{assignment.classroom}-{assignment.slug}-{user}"
        info = self.gh.get(f"/repos/{org}/{repo}")
        branch = info["default_branch"]
        params = {"sha": branch, "per_page": 1}
        if until:
            params["until"] = until
        commits = self.gh.get(f"/repos/{org}/{repo}/commits", **params)
        if not commits:
            raise GitHubError(404, f"{repo} has no commits" + (f" before {until}" if until else ""))
        c = commits[0]
        return Submission(organization=org, classroom=assignment.classroom,
                          assignment=assignment.slug, user=user, repo=repo, sha=c["sha"],
                          date=iso_date(c["commit"]["committer"]["date"]), branch=branch,
                          web=self.cfg.web)

    # ─── Assignment template ─────────────────────────────────────────────────

    def template(self, assignment: Assignment) -> Template:
        """The extracted template at its current version (refreshed every `template_ttl` s)."""
        key = (assignment.template, assignment.ref)
        with self._lock:
            cached = self._templates.get(key)
            if cached and time.monotonic() - cached[0] < self.template_ttl:
                return cached[1]
            owner, repo = assignment.template.split("/", 1)
            sha = self.gh.get(f"/repos/{owner}/{repo}/commits/{assignment.ref}")["sha"]
            if cached and cached[1].sha == sha:
                self._templates[key] = (time.monotonic(), cached[1])
                return cached[1]
            folder = self.cfg.work_dir / "templates" / f"{owner}-{repo}-{sha[:12]}"
            if not folder.is_dir():
                temporary = folder.with_name(folder.name + ".tmp")
                shutil.rmtree(temporary, ignore_errors=True)
                extract_tarball(self.gh.download_tarball(owner, repo, sha), temporary)
                temporary.rename(folder)
            t = Template(repo=assignment.template, sha=sha, dir=folder,
                         settings=read_settings(folder / assignment.grader_dir))
            self._templates[key] = (time.monotonic(), t)
            return t

    def download(self, submission: Submission, dest: Path) -> None:
        extract_tarball(self.gh.download_tarball(submission.organization, submission.repo,
                                                 submission.sha), dest)


def read_settings(grader_dir: Path) -> dict:
    """The `[assignment]` table of `<grader_dir>/assignment.toml`."""
    path = grader_dir / "assignment.toml"
    if not path.is_file():
        raise FileNotFoundError(f"the template has no {path.name} in {grader_dir.name}/")
    return tomllib.loads(path.read_text(encoding="utf-8")).get("assignment", {})


def prepare_folder(student_code: Path, template: Template, grader_dir: str) -> list:
    """Replaces the grader folder and the protected files in the submission with those of the
    template. Returns the list of protected files that were restored (for the report)."""
    dest = student_code / grader_dir
    shutil.rmtree(dest, ignore_errors=True)
    if dest.exists():
        dest.unlink()
    shutil.copytree(template.dir / grader_dir, dest)
    restored = []
    for pattern in template.settings.get("protected", []):
        for source in sorted(template.dir.rglob("*")):
            relative = source.relative_to(template.dir).as_posix()
            if source.is_file() and fnmatch.fnmatch(relative, pattern):
                copy = student_code / relative
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copy)
                restored.append(relative)
    return restored
