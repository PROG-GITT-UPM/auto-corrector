"""Publishing a grading result without using GitHub Actions.

1. **Release in the student's repository** (`grading/<date>-<commit>`): the student sees the
   result of each test. It carries `result.json` as an attachment.
2. **Score in Classroom 50**: an `"override": true` entry in `<classroom>/scores.json` of the
   `classroom50` repository. It is exactly what the Classroom 50 web writes when a teacher enters
   a score by hand (`web/src/domain/assignments/scoreOverride.ts`), so the score shows up on the
   submissions page and in the "Download scores" CSV, and the score collector leaves it alone
   (`collect_scores.py` keeps entries with override).

Every commit in the `classroom50` repository triggers its `publish-pages` workflow, which does
spend Actions minutes. That is why scores are accumulated and written in ONE commit per
classroom, with `[skip ci]` in the message (GitHub does not run push workflows for those commits).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .github import Conflict, GitHub, GitHubError
from .submissions import Submission

log = logging.getLogger(__name__)

TAG_PREFIX = "grading/"
SUBMISSION_PREFIX = "submit/manual-grader-"
# Prefixes of scores written by earlier versions of the grader: they are still ours, not the
# teacher's, so a new grading run may replace them.
LEGACY_SUBMISSION_PREFIXES = ("submit/manual-corrector-",)
OWN_SUBMISSION_PREFIXES = (SUBMISSION_PREFIX, *LEGACY_SUBMISSION_PREFIXES)
MANUAL_PREFIX = "submit/manual-"
MAX_HISTORY = 20


def folder_fingerprint(folder: Path) -> str:
    """Fingerprint of the tests: if they change, already graded submissions are graded again."""
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file()):
        h.update(f.relative_to(folder).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def fingerprint_marker(fingerprint: str) -> str:
    return f"<!-- grader:fingerprint={fingerprint} -->"


# ─── Release ─────────────────────────────────────────────────────────────────

def existing_release(gh: GitHub, submission: Submission, fingerprint: str) -> Optional[dict]:
    """The Release of an earlier grading run of the same commit with the same tests, if any."""
    try:
        releases = gh.get(f"/repos/{submission.organization}/{submission.repo}/releases",
                          per_page=50)
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise
    for r in releases:
        tag = r.get("tag_name", "")
        if (tag.startswith(TAG_PREFIX) and tag.endswith(f"-{submission.sha7}")
                and fingerprint_marker(fingerprint) in (r.get("body") or "")):
            return r
    return None


def publish_release(gh: GitHub, submission: Submission, body: str, result: dict,
                    fingerprint: str, stamp: str) -> str:
    """Creates the Release (and its tag) on the graded commit; returns its URL."""
    tag = f"{TAG_PREFIX}{stamp.replace(':', '-')}-{submission.sha7}"
    points, maximum = result["score"], result["max-score"]
    release = gh.send("POST", f"/repos/{submission.organization}/{submission.repo}/releases", {
        "tag_name": tag,
        "target_commitish": submission.sha,
        "name": f"Grading: {points}/{maximum} · {submission.sha7}",
        "body": f"{body}\n\n{fingerprint_marker(fingerprint)}",
        "make_latest": "true",
    })
    result["release"] = release["html_url"]
    gh.upload_asset(release["upload_url"], "result.json",
                    json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
    return release["html_url"]


# ─── Scores in scores.json ───────────────────────────────────────────────────

def _is_own(record: dict) -> bool:
    return str(record.get("submission", "")).startswith(OWN_SUBMISSION_PREFIXES)


def _is_teacher_manual(record: dict) -> bool:
    return str(record.get("submission", "")).startswith(MANUAL_PREFIX) and not _is_own(record)


def apply_score(scores: dict, slug: str, assignment_type: str, record: dict,
                force: bool = False) -> str:
    """Puts a grading record into `scores` (modifies the dict). Returns what it did: "new",
    "updated" or "kept" (if the teacher entered a score by hand on the web, it is not overwritten
    unless `force` is set)."""
    scores.setdefault("schema", "classroom50/scores/v1")
    buckets = scores.setdefault("assignments", {})
    bucket = buckets.setdefault(slug, {"type": assignment_type, "entries": []})
    bucket["type"] = assignment_type
    entries = bucket.setdefault("entries", [])
    owner = record["owner"].strip().lower()
    record = {k: v for k, v in record.items() if k != "assignment"}
    for i, entry in enumerate(entries):
        if str(entry.get("owner", "")).strip().lower() != owner:
            continue
        previous = entry.get("submissions") or []
        if previous and _is_teacher_manual(previous[0]) and not force:
            return "kept"
        # History: earlier grader runs that are not later than this one (when grading what was
        # submitted on time with --until, whatever came afterwards must not stay in front), and
        # submissions autograded by Classroom 50, if there are any.
        earlier = [s for s in previous if not _is_teacher_manual(s)
                   and not (_is_own(s) and s.get("datetime", "") > record["datetime"])]
        entries[i] = {**entry, "owner": entry.get("owner") or record["owner"],
                      "override": True,
                      "submissions": [record] + earlier[:MAX_HISTORY - 1]}
        return "updated"
    entries.append({"owner": record["owner"], "override": True, "submissions": [record]})
    return "new"


class ScoreBook:
    """Accumulates scores and writes them to scores.json in a single commit per classroom."""

    def __init__(self, gh: GitHub, organization: str, config_repo: str) -> None:
        self.gh = gh
        self.organization = organization
        self.config_repo = config_repo
        self._pending: dict = {}
        self._lock = threading.Lock()

    def add(self, classroom: str, slug: str, assignment_type: str, record: dict,
            force: bool = False) -> None:
        with self._lock:
            self._pending[(classroom, slug, record["owner"].lower())] = (
                assignment_type, record, force)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def save(self, attempts: int = 6) -> dict:
        """Writes what is pending. Returns {(classroom, assignment, user): "new"|"updated"|...}."""
        with self._lock:
            pending, self._pending = self._pending, {}
        by_classroom: dict = {}
        for (classroom, slug, user), value in pending.items():
            by_classroom.setdefault(classroom, []).append((slug, user, value))
        statuses: dict = {}
        for classroom, items in by_classroom.items():
            path = f"{classroom}/scores.json"
            for attempt in range(attempts):
                try:
                    try:
                        text, sha = self.gh.read_file(self.organization, self.config_repo, path)
                        scores = json.loads(text)
                    except GitHubError as exc:
                        if exc.status != 404:
                            raise
                        scores, sha = {"schema": "classroom50/scores/v1", "assignments": {}}, None
                    summary = {}
                    for slug, user, (assignment_type, record, force) in items:
                        summary[(classroom, slug, user)] = apply_score(
                            scores, slug, assignment_type, record, force)
                    changes = [k for k, v in summary.items() if v != "kept"]
                    if changes:
                        message = (f"Grader: {len(changes)} score(s) in {classroom} "
                                   f"({', '.join(sorted({k[1] for k in changes}))}) [skip ci]")
                        self.gh.write_file(
                            self.organization, self.config_repo, path,
                            json.dumps(scores, indent=2, ensure_ascii=False) + "\n", message, sha)
                    statuses.update(summary)
                    break
                except Conflict:
                    log.info("scores.json of %s changed while writing; retrying (%d)",
                             classroom, attempt + 1)
            else:
                # They are not lost: they go back to the queue for the next attempt.
                with self._lock:
                    for slug, user, value in items:
                        self._pending.setdefault((classroom, slug, user), value)
                raise GitHubError(409, f"could not write {path} after {attempts} attempts")
        return statuses


def is_late(date: str, deadline: Optional[str]) -> Optional[bool]:
    if not deadline:
        return None
    a = datetime.fromisoformat(date.replace("Z", "+00:00"))
    b = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    return a > b
