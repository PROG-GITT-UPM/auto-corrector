"""Request and response models of the API. Their descriptions and examples are what the API docs
(/docs) show for each endpoint."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,99}$"


# ─── Assignments ─────────────────────────────────────────────────────────────

class AssignmentIn(BaseModel):
    template: str = Field(
        description="Assignment repository, as `organization/repository`. The grader reads the "
                    "visible tests from its `.grader/` folder.",
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", examples=["PROG-GITT-UPM/prog-lab-2"])
    ref: str = Field("main", description="Branch, tag or commit of the template to read the "
                                         "tests from.")
    publish_release: bool = Field(True, description="Publish a Release with the result in the "
                                                    "student's repository.")
    save_score: bool = Field(True, description="Write the score to Classroom 50.")


class WatchIn(BaseModel):
    enabled: bool = Field(False, description="Grade every push automatically. Off by default: "
                                             "submissions are only graded when asked through "
                                             "`POST /grade`.")
    interval_seconds: int = Field(120, ge=30, le=86400,
                                  description="How often to check for new pushes.")


class WatchOut(BaseModel):
    enabled: bool
    interval_seconds: int
    last_round_at: Optional[datetime] = Field(None, description="When pushes were last checked.")


class AssignmentOut(BaseModel):
    classroom: str = Field(examples=["grupo-test"])
    assignment: str = Field(examples=["lab-2"])
    template: str
    ref: str
    publish_release: bool
    save_score: bool
    hidden_tests: int = Field(description="Number of hidden tests.")
    watch: WatchOut
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = Field(default_factory=list,
                                description="Problems found when saving the assignment.")


class HiddenTestsIn(BaseModel):
    tests: list[dict[str, Any]] = Field(
        description="Tests only the grader runs, in the Classroom 50 format (the same as "
                    "`.grader/tests.json`). They are added to the visible ones. `input` and "
                    "`expected` go inline: `input-file` and `expected-file` are not supported. "
                    "Students see in their Release whether each hidden test passed, but not "
                    "its input, expected output or their own output.",
        examples=[[{"name": "Ejercicio 1 — Validador de número de teléfono · caso oculto 1",
                    "type": "io", "run": "python exercise_01_phone.py",
                    "input": "612345678\n", "expected": "Es móvil:              True",
                    "comparison": "included", "timeout": 10, "points": 1}]])


class HiddenTestsOut(BaseModel):
    tests: list[dict[str, Any]]
    points: int = Field(description="Total points of the hidden tests.")


class SyncRequest(BaseModel):
    classroom: Optional[str] = Field(None, description="Only this classroom. Leave it out to "
                                                       "sync every classroom.",
                                     examples=["grupo-test"])
    remove_missing: bool = Field(False, description="Remove the assignments that Classroom 50 no "
                                                    "longer has or that are no longer gradable. "
                                                    "Their hidden tests are removed with them.")
    dry_run: bool = Field(False, description="Only report what would change.")


class SyncItemOut(BaseModel):
    classroom: str
    assignment: str
    action: Literal["added", "updated", "unchanged", "skipped", "missing", "removed"] = Field(
        description="`skipped`: declared in Classroom 50 but not gradable here (`reason` says "
                    "why). `missing`: configured here but not gradable in Classroom 50 any more; "
                    "kept unless `remove_missing`.")
    reason: str = ""
    template: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class StudentOut(BaseModel):
    user: str = Field(description="GitHub username.", examples=["ana"])
    repo: str = Field(examples=["grupo-test-lab-2-ana"])
    pushed_at: Optional[str] = Field(None, description="Last push to the repository.")


# ─── Grading ─────────────────────────────────────────────────────────────────

class GradeRequest(BaseModel):
    classroom: str = Field(examples=["grupo-test"])
    assignment: str = Field(examples=["lab-2"])
    students: Optional[list[str]] = Field(
        None, description="GitHub usernames. Leave it out to grade every student who accepted "
                          "the assignment.", examples=[["ana", "luis"]])
    until: Optional[datetime] = Field(
        None, description="Grade the latest commit before this date (what was submitted on "
                          "time), instead of the latest one.", examples=["2026-11-15T22:59:00Z"])
    force: bool = Field(False, description="Grade even if that commit was already graded with "
                                           "the same tests, and overwrite scores a teacher "
                                           "entered by hand on the Classroom 50 web.")


class GradeResponse(BaseModel):
    jobs: list[str] = Field(description="Ids of the queued jobs.")
    students: int = Field(description="Students the request covered.")
    already_queued: list[str] = Field(
        description="Students who already had a job waiting: it was not queued twice.")


# ─── Jobs ────────────────────────────────────────────────────────────────────

class TestOut(BaseModel):
    name: str
    passed: bool
    points: int
    max_points: int
    reason: str = ""


class JobOut(BaseModel):
    id: str
    classroom: str
    assignment: str
    user: str
    source: Literal["api", "watch"] = Field(description="Who asked for it: the API or the push "
                                                        "watcher.")
    status: Literal["queued", "grading", "done", "error"]
    force: bool
    until: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    outcome: Optional[str] = Field(None, description="`graded`, or `already graded` if that "
                                                     "commit had been graded with the same tests.")
    commit: Optional[str] = None
    points: Optional[int] = None
    max_points: Optional[int] = None
    late: Optional[bool] = None
    release_url: Optional[str] = None
    tests: Optional[list[TestOut]] = None
    error: Optional[str] = None


# ─── Scores ──────────────────────────────────────────────────────────────────

class PendingScoreOut(BaseModel):
    classroom: str
    assignment: str
    user: str
    score: int
    max_score: int
    graded_at: str
    queued_at: datetime


class SavedScoreOut(BaseModel):
    classroom: str
    assignment: str
    user: str
    result: Literal["new", "updated", "kept"] = Field(
        description="`kept`: the student has a score a teacher entered by hand on the web, and "
                    "it was not overwritten.")


# ─── Health ──────────────────────────────────────────────────────────────────

class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    sandbox_image: Optional[bool] = Field(None, description="Whether the sandbox image exists "
                                                            "(null: Docker not reachable).")
    queued: int
    grading: int
    pending_scores: int
    watched_assignments: int
