"""Assignments: checking one before it is saved, and syncing them with Classroom 50.

Classroom 50 keeps its configuration in the `classroom50` repository of the organization: one
folder per classroom with an `assignments.json`. Each assignment names its template as
`{"owner", "repo", "branch"}`. An assignment can be graded by this service when it is individual,
has a template, and was created with "Do not use the built-in autograder" (`no_autograder`):
otherwise every push would also spend Actions minutes on the Classroom 50 autograder.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from . import engine
from ..providers.database import Assignment
from ..providers.github import GitHubError
from .grading import Context, hidden_tests, visible_tests

log = logging.getLogger(__name__)


class AssignmentCheckError(ValueError):
    """The assignment cannot be graded by this service (the message says why)."""


def check_assignment(ctx: Context, classroom: str, slug: str, template: str, ref: str,
                     hidden: Optional[list] = None,
                     classroom_data: Optional[dict] = None) -> list:
    """Checks that an assignment can be graded. Returns warnings; raises AssignmentCheckError if
    it cannot be graded at all."""
    if classroom_data is None:
        try:
            classroom_data = ctx.repos.classroom_assignment(classroom, slug)
        except GitHubError as exc:
            raise AssignmentCheckError(f"Classroom 50 assignment not found: {exc.message}") from None
    if classroom_data.get("mode", "individual") != "individual":
        raise AssignmentCheckError("only individual assignments can be graded for now")
    warnings = []
    if classroom_data.get("no_autograder") is not True:
        warnings.append("the assignment does not have \"Do not use the built-in autograder\": "
                        "every push spends Actions minutes on the Classroom 50 autograder")
    if (classroom_data.get("grading") or {}).get("mode") not in (None, "manual"):
        warnings.append("the assignment's grading mode is not \"manual\"")
    candidate = Assignment(classroom=classroom, slug=slug, template=template, ref=ref,
                           hidden_tests=hidden)
    try:
        tmpl = ctx.repos.template(candidate)
        visible = visible_tests(tmpl.dir, candidate, tmpl.settings)
        extra = hidden_tests(candidate)
    except (GitHubError, FileNotFoundError, engine.TestFileError) as exc:
        raise AssignmentCheckError(f"the template's tests could not be loaded: {exc}") from None
    max_points = (classroom_data.get("grading") or {}).get("max_points")
    total = sum(t.points for t in visible) + sum(t.points for t in extra)
    if max_points is not None and max_points != total:
        warnings.append(f"Classroom 50 expects {max_points} points but the tests add up to "
                        f"{total}")
    return warnings


def template_of(classroom_data: dict) -> Optional[tuple]:
    """(\"owner/repo\", branch) of a Classroom 50 assignment, or None if it has no template."""
    t = classroom_data.get("template")
    if not isinstance(t, dict) or not t.get("owner") or not t.get("repo"):
        return None
    return f"{t['owner']}/{t['repo']}", t.get("branch") or "main"


@dataclass
class SyncItem:
    classroom: str
    assignment: str
    action: str          # added, updated, unchanged, skipped, missing, removed
    reason: str = ""
    template: Optional[str] = None
    warnings: list = field(default_factory=list)


def list_classrooms(ctx: Context) -> list:
    """Classroom slugs: the folders of the `classroom50` repository with an assignments.json."""
    s = ctx.settings
    entries = ctx.gh.get(f"/repos/{s.organization}/{s.config_repo}/contents")
    classrooms = []
    for entry in entries:
        if entry.get("type") != "dir" or entry["name"].startswith("."):
            continue
        try:
            ctx.repos.classroom_assignments(entry["name"])
        except GitHubError as exc:
            if exc.status == 404:
                continue
            raise
        classrooms.append(entry["name"])
    return classrooms


def sync(ctx: Context, classroom: Optional[str] = None, remove_missing: bool = False,
         dry_run: bool = False) -> list:
    """Brings the service's assignments in line with Classroom 50.

    * Gradable assignments that the service does not have are added (watching off).
    * Assignments whose template or branch changed in Classroom 50 are updated; their hidden
      tests, watch settings and Release/score options are kept.
    * Assignments the service has but Classroom 50 no longer has (or that are no longer
      gradable) are reported as `missing`, and removed only with `remove_missing`: removing an
      assignment also removes its hidden tests.
    """
    db = ctx.db
    classrooms = [classroom] if classroom else list_classrooms(ctx)
    items: list = []
    for room in classrooms:
        try:
            declared = ctx.repos.classroom_assignments(room)
        except GitHubError as exc:
            raise AssignmentCheckError(f"classroom {room} not found in Classroom 50: "
                                       f"{exc.message}") from None
        current = {a.slug: a for a in db.list_assignments() if a.classroom == room}
        gradable = set()
        for data in declared:
            slug = data.get("slug")
            if not slug:
                continue
            item = SyncItem(classroom=room, assignment=slug, action="skipped")
            items.append(item)
            found = template_of(data)
            if found is None:
                item.reason = "it has no template"
                continue
            template, ref = found
            item.template = template
            if data.get("mode", "individual") != "individual":
                item.reason = "group assignments are not graded yet"
                continue
            if data.get("no_autograder") is not True:
                item.reason = ("it uses the Classroom 50 autograder (\"Do not use the built-in "
                               "autograder\" is off)")
                continue
            existing = current.get(slug)
            try:
                item.warnings = check_assignment(
                    ctx, room, slug, template, ref,
                    existing.hidden_tests if existing else None, classroom_data=data)
            except AssignmentCheckError as exc:
                item.reason = str(exc)
                continue
            gradable.add(slug)
            if existing is None:
                item.action = "added"
                if not dry_run:
                    db.upsert_assignment(room, slug, template, ref, True, True)
            elif (existing.template, existing.ref) != (template, ref):
                item.action = "updated"
                item.reason = f"template was {existing.template}@{existing.ref}"
                if not dry_run:
                    db.upsert_assignment(room, slug, template, ref, existing.publish_release,
                                         existing.save_score)
            else:
                item.action = "unchanged"
        for slug, existing in current.items():
            if slug in gradable:
                continue
            skipped = next((i for i in items if i.classroom == room and i.assignment == slug),
                           None)
            item = SyncItem(classroom=room, assignment=slug, template=existing.template,
                            action="missing",
                            reason=(f"no longer gradable: {skipped.reason}" if skipped
                                    else "not in Classroom 50 any more"))
            if remove_missing:
                item.action = "removed"
                if not dry_run:
                    db.delete_assignment(room, slug)
            # A missing assignment that is still declared already has a "skipped" item.
            items = [i for i in items
                     if not (i.classroom == room and i.assignment == slug)] + [item]
    if not dry_run:
        log.info("sync with Classroom 50: %s",
                 ", ".join(f"{i.classroom}/{i.assignment} {i.action}" for i in items
                           if i.action not in ("unchanged", "skipped")) or "no changes")
    return items
