"""One grading run from start to finish: submission → prepared folder → tests in the container →
Release for the student → score pending to be saved in Classroom 50."""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import engine, publish, report
from .config import Assignment, Config
from .github import GitHub
from .sandbox import create_sandbox
from .submissions import Repositories, prepare_folder

log = logging.getLogger(__name__)


class GradingError(RuntimeError):
    pass


@dataclass
class Context:
    cfg: Config
    gh: GitHub = field(init=False)
    repos: Repositories = field(init=False)
    scores: publish.ScoreBook = field(init=False)

    def __post_init__(self) -> None:
        self.gh = GitHub(self.cfg.token, api=self.cfg.api, web=self.cfg.web)
        self.repos = Repositories(self.gh, self.cfg)
        self.scores = publish.ScoreBook(self.gh, self.cfg.organization, self.cfg.config_repo)


def tests_for(folder: Path, assignment: Assignment, settings: dict) -> list:
    """The template tests (the ones the student sees) plus the hidden ones, if any."""
    grader_dir = folder / assignment.grader_dir
    tests = engine.load_tests(grader_dir / settings.get("tests", "tests.json"))
    if assignment.extra_tests:
        names = {t.name for t in tests}
        extra = engine.load_tests(assignment.extra_tests)
        repeated = names & {t.name for t in extra}
        if repeated:
            raise GradingError(f"hidden tests with the same name as visible ones: {repeated}")
        tests += extra
    return tests


def grade_submission(ctx: Context, assignment: Assignment, user: str, until: Optional[str] = None,
                     force: bool = False, publish_result: bool = True) -> dict:
    """Grades a student's submission. Returns a summary (for the log or the API)."""
    cfg = ctx.cfg
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
    fingerprint = publish.folder_fingerprint(template.dir / assignment.grader_dir)
    if assignment.extra_tests:
        fingerprint = hashlib.sha256(
            (fingerprint + assignment.extra_tests.read_text(encoding="utf-8")).encode()
        ).hexdigest()[:16]
    summary = {"classroom": assignment.classroom, "assignment": assignment.slug, "user": user,
               "repo": submission.repo, "commit": submission.sha7, "date": submission.date}

    if publish_result and assignment.publish_release and not force:
        previous = publish.existing_release(ctx.gh, submission, fingerprint)
        if previous:
            return {**summary, "status": "already graded", "release": previous["html_url"]}

    folder = (cfg.work_dir / "submissions"
              / f"{submission.repo}-{submission.sha7}-{uuid.uuid4().hex[:6]}")
    try:
        ctx.repos.download(submission, folder)
        restored = prepare_folder(folder, template, assignment.grader_dir)
        tests = tests_for(folder, assignment, template.settings)
        log.info("grading %s@%s (%d tests)", submission.repo, submission.sha7, len(tests))
        with create_sandbox(cfg.sandbox, folder) as box:
            result = engine.evaluate(tests, box.run, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    stamp = report.now_iso()
    submission_id = publish.SUBMISSION_PREFIX + stamp.replace(":", "-")
    data = report.result_json(submission, template, result, release_url="",
                              submission_id=submission_id, assignment_type=mode, graded_at=stamp)
    late = publish.is_late(submission.date, classroom_data.get("due"))
    summary.update(status="graded", points=result.points, max_points=result.max_points, late=late)
    if not publish_result:
        summary["result"] = result.to_dict()
        return summary

    if assignment.publish_release:
        body = report.release_body(submission, template, result, restored)
        summary["release"] = publish.publish_release(ctx.gh, submission, body, data, fingerprint,
                                                     stamp)
    if assignment.save_score:
        record = {**data, "grader": report.grader_data(template)}
        if late is not None:
            record["late"] = late
        ctx.scores.add(assignment.classroom, assignment.slug, mode, record, force=force)
        summary["score"] = "pending save"
    return summary
