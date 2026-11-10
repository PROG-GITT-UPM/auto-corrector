"""One grading run from start to finish: submission → prepared folder → tests in the container →
Release for the student → score pending to be saved in Classroom 50."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import engine, publish, report
from ..config.settings import Settings
from ..providers.database import Assignment, Database
from ..providers.github import GitHub
from ..providers.sandbox import create_sandbox
from .submissions import Repositories, prepare_folder

log = logging.getLogger(__name__)


class GradingError(RuntimeError):
    pass


@dataclass
class Context:
    """Everything a grading run needs: settings, GitHub, the database and the score book."""
    settings: Settings
    db: Database
    gh: GitHub = field(init=False)
    repos: Repositories = field(init=False)
    scores: publish.ScoreBook = field(init=False)

    def __post_init__(self) -> None:
        s = self.settings
        self.gh = GitHub(s.github_token, api=s.github_api, web=s.github_web)
        self.repos = Repositories(self.gh, s)
        self.scores = publish.ScoreBook(self.gh, s.organization, s.config_repo, self.db)


def visible_tests(folder: Path, assignment: Assignment, settings: dict) -> list:
    """The template tests: the ones the student runs with check.py."""
    grader_dir = folder / assignment.grader_dir
    return engine.load_tests(grader_dir / settings.get("tests", "tests.json"))


def hidden_tests(assignment: Assignment) -> list:
    """Tests only the grader runs, stored with the assignment. `*-file` fields are not
    supported here: there is no folder for them, so input and expected text go inline."""
    if not assignment.hidden_tests:
        return []
    return engine.parse_tests(assignment.hidden_tests, Path("/nonexistent"))


def tests_for(folder: Path, assignment: Assignment, settings: dict) -> list:
    """Visible tests followed by the hidden ones."""
    tests = visible_tests(folder, assignment, settings)
    extra = hidden_tests(assignment)
    repeated = {t.name for t in tests} & {t.name for t in extra}
    if repeated:
        raise GradingError(f"hidden tests with the same name as visible ones: {sorted(repeated)}")
    return tests + extra


def fingerprint(template_dir: Path, assignment: Assignment) -> str:
    """Changes when the visible or the hidden tests change, so that graded submissions are graded
    again with the new tests."""
    base = publish.folder_fingerprint(template_dir / assignment.grader_dir)
    if not assignment.hidden_tests:
        return base
    hidden = json.dumps(assignment.hidden_tests, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((base + hidden).encode()).hexdigest()[:16]


def grade_submission(ctx: Context, assignment: Assignment, user: str, until: Optional[str] = None,
                     force: bool = False, publish_result: bool = True) -> dict:
    """Grades a student's submission. Returns a summary (stored with the job)."""
    settings = ctx.settings
    classroom_data = ctx.repos.classroom_assignment(assignment.classroom, assignment.slug)
    mode = classroom_data.get("mode", "individual")
    if mode != "individual":
        raise GradingError(f"{assignment.key} is a {mode} assignment: for now the grader only "
                           "grades individual assignments")
    if classroom_data.get("no_autograder") is not True:
        log.warning("%s does not have \"Do not use the built-in autograder\": every push will "
                    "spend Actions minutes on the Classroom 50 autograder", assignment.key)

    submission = ctx.repos.resolve(assignment, user, until)
    template = ctx.repos.template(assignment)
    print_ = fingerprint(template.dir, assignment)
    summary = {"classroom": assignment.classroom, "assignment": assignment.slug, "user": user,
               "repo": submission.repo, "commit": submission.sha7, "date": submission.date}

    if publish_result and assignment.publish_release and not force:
        previous = publish.existing_release(ctx.gh, submission, print_)
        if previous:
            return {**summary, "status": "already graded", "release": previous["html_url"]}

    folder = (settings.work_dir / "submissions"
              / f"{submission.repo}-{submission.sha7}-{uuid.uuid4().hex[:6]}")
    try:
        ctx.repos.download(submission, folder)
        restored = prepare_folder(folder, template, assignment.grader_dir)
        tests = tests_for(folder, assignment, template.settings)
        log.info("grading %s@%s (%d tests)", submission.repo, submission.sha7, len(tests))
        with create_sandbox(settings.sandbox, folder) as box:
            result = engine.evaluate(tests, box.run, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    hidden = frozenset(t.name for t in hidden_tests(assignment))
    stamp = report.now_iso()
    submission_id = publish.SUBMISSION_PREFIX + stamp.replace(":", "-")
    data = report.result_json(submission, template, result, release_url="",
                              submission_id=submission_id, assignment_type=mode, graded_at=stamp,
                              hidden=hidden)
    late = publish.is_late(submission.date, classroom_data.get("due"))
    summary.update(status="graded", points=result.points, max_points=result.max_points, late=late,
                   tests=[{"name": r.name, "passed": r.passed, "points": r.points,
                           "max_points": r.max_points, "reason": r.reason}
                          for r in result.tests])
    if not publish_result:
        return summary

    if assignment.publish_release:
        body = report.release_body(submission, template, result, restored, hidden)
        summary["release"] = publish.publish_release(ctx.gh, submission, body, data, print_, stamp)
    if assignment.save_score:
        record = {**data, "grader": report.grader_data(template)}
        if late is not None:
            record["late"] = late
        ctx.scores.add(assignment.classroom, assignment.slug, mode, record, force=force)
        summary["score"] = "pending save"
    return summary
