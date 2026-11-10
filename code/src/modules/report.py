"""What a grading run produces: `result.json` (classroom50/result/v1 contract) and the text of the
Release the student sees."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import engine
from ..utilities.constants import VERSION as __version__
from .submissions import Submission, Template

BODY_LIMIT = 120_000  # GitHub accepts 125,000 characters in a Release body


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def result_json(submission: Submission, template: Template, result: engine.Result,
                release_url: str, submission_id: str, assignment_type: str = "individual",
                graded_at: Optional[str] = None, hidden: frozenset = frozenset()) -> dict:
    """The result in the Classroom 50 format (classroom50/result/v1).

    `submission_id` identifies the submission. For the Classroom 50 web to treat it as a manual
    score (one that can be deleted from the web) it starts with "submit/manual-"."""
    return {
        "schema": "classroom50/result/v1",
        "classroom": submission.classroom,
        "assignment": submission.assignment,
        "assignment_type": assignment_type,
        "owner": submission.user,
        "submission": submission_id,
        "commit": submission.commit_url,
        "release": release_url or submission.commit_url,
        "review": submission.review_url,
        "datetime": submission.date,
        "graded_at": graded_at or now_iso(),
        "score": result.points,
        "max-score": result.max_points,
        "tests": [_redact_row(r.to_classroom50()) if r.name in hidden else r.to_classroom50()
                  for r in result.tests],
    }


HIDDEN_DETAIL = "hidden test: details are not shown"


def _redact_row(row: dict) -> dict:
    """A hidden test's row says whether it passed, but not why it failed."""
    if "detail" in row:
        row["detail"] = HIDDEN_DETAIL
    return row


def grader_data(template: Template) -> dict:
    """Our own field for the scores.json record (extra fields are allowed there; not in
    result.json, whose classroom50/result/v1 schema rejects them)."""
    return {"version": __version__, "engine": engine.ENGINE_VERSION,
            "template": template.repo, "template_sha": template.sha}


def _block(text: str) -> str:
    text = engine.truncate(text)
    fence = "````" if "```" in text else "```"
    return f"{fence}text\n{text}\n{fence}"


def release_body(submission: Submission, template: Template, result: engine.Result,
                 restored: list, hidden: frozenset = frozenset()) -> str:
    """The text of the Release, written for a first-year student."""
    p, m = result.points, result.max_points
    title = "All tests passed" if p == m and m else f"{p} of {m} points"
    lines = [
        f"## {title}",
        "",
        f"Grading of commit [`{submission.sha7}`]({submission.commit_url}) "
        f"({submission.date.replace('T', ' ').replace('Z', ' UTC')}).",
        "",
        "| | Test | Points |",
        "|---|---|---|",
    ]
    for r in result.tests:
        mark = "✅" if r.passed else "❌"
        lines.append(f"| {mark} | {r.name} | {r.points}/{r.max_points} |")
    failures = [r for r in result.tests if not r.passed]
    if failures:
        lines += ["", "### What failed", "",
                  "You can run each visible test again on your computer with `python check.py`."]
        for r in failures:
            if r.name in hidden:
                lines += ["", f"<b>{r.name}</b>: {HIDDEN_DETAIL}."]
                continue
            lines += ["", "<details>", f"<summary><b>{r.name}</b>: {r.reason}</summary>", ""]
            if r.type == "io":
                if r.input:
                    lines += ["Typed on the keyboard:", "", _block(r.input), ""]
                difference = engine.explain_difference(r.expected, r.output) if r.output else ""
                if difference:
                    lines += ["Where it differs:", "", _block(difference), ""]
                lines += ["Expected output:", "", _block(r.expected), "",
                          "Your output:", "", _block(r.output or "(nothing)"), ""]
                if r.comparison == "included":
                    lines += ["Your output may contain more text (for example, the `input()` "
                              "prompts), but it must contain the expected output exactly as is, "
                              "with the same spaces.", ""]
            elif r.output:
                lines += ["Output:", "", _block(r.output), ""]
            if r.error.strip():
                lines += ["Program error:", "", _block(r.error), ""]
            lines.append("</details>")
    if restored:
        lines += ["", "The original assignment files were used for grading instead of yours: "
                      f"{', '.join(f'`{f}`' for f in restored)}."]
    lines += ["", "---",
              f"<sub>Graded on the course servers · tests from "
              f"`{template.repo}@{template.sha7}` · grader {__version__}</sub>"]
    body = "\n".join(lines)
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT] + "\n\n… (report truncated: it is too long)"
    return body
