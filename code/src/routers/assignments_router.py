# external imports
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

# internal imports
from ..data_models.api import (AssignmentIn, AssignmentOut, HiddenTestsIn, HiddenTestsOut,
                               StudentOut, WatchIn, WatchOut)
from ..modules import engine
from ..modules.grading import hidden_tests as parse_hidden, visible_tests
from ..providers.database import Assignment
from ..providers.github import GitHubError
from ..utilities.endpoints_tags import ASSIGNMENTS_TAG, BASE_ASSIGNMENTS_ENDPOINT
from ..utilities.security import require_token

assignments_router = APIRouter(prefix=BASE_ASSIGNMENTS_ENDPOINT, tags=[ASSIGNMENTS_TAG],
                               dependencies=[Depends(require_token)])

ITEM = "/{classroom}/{assignment}"


def _out(a: Assignment, warnings: list | None = None) -> AssignmentOut:
    return AssignmentOut(
        classroom=a.classroom, assignment=a.slug, template=a.template, ref=a.ref,
        publish_release=a.publish_release, save_score=a.save_score,
        hidden_tests=len(a.hidden_tests or []),
        watch=WatchOut(enabled=a.watch_enabled, interval_seconds=a.watch_interval,
                       last_round_at=a.last_watch_at),
        created_at=a.created_at, updated_at=a.updated_at, warnings=warnings or [])


def _get(request: Request, classroom: str, assignment: str) -> Assignment:
    a = request.app.state.service.db.get_assignment(classroom, assignment)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"assignment {classroom}/{assignment} is not configured")
    return a


@assignments_router.get("", response_model=list[AssignmentOut],
                        summary="List the assignments graded by this service")
def list_assignments(request: Request) -> list[AssignmentOut]:
    return [_out(a) for a in request.app.state.service.db.list_assignments()]


@assignments_router.get(ITEM, response_model=AssignmentOut, summary="Get an assignment")
def get_assignment(request: Request, classroom: str, assignment: str) -> AssignmentOut:
    return _out(_get(request, classroom, assignment))


@assignments_router.put(
    ITEM, response_model=AssignmentOut, summary="Add or update an assignment",
    description="`classroom` and `assignment` are the Classroom 50 slugs. Before saving, the "
                "service checks that the assignment exists in Classroom 50 and is individual, "
                "and that the template's tests load. It warns if the assignment still uses the "
                "Classroom 50 autograder (every push would spend Actions minutes). Watching and "
                "hidden tests are kept when the assignment is updated.")
def put_assignment(request: Request, classroom: str, assignment: str,
                   body: AssignmentIn) -> AssignmentOut:
    service = request.app.state.service
    ctx = service.ctx
    warnings = []
    try:
        data = ctx.repos.classroom_assignment(classroom, assignment)
    except GitHubError as exc:
        raise HTTPException(422,
                            f"Classroom 50 assignment not found: {exc.message}") from None
    if data.get("mode", "individual") != "individual":
        raise HTTPException(422,
                            "only individual assignments can be graded for now")
    if data.get("no_autograder") is not True:
        warnings.append("the assignment does not have \"Do not use the built-in autograder\": "
                        "every push spends Actions minutes on the Classroom 50 autograder")
    if (data.get("grading") or {}).get("mode") not in (None, "manual"):
        warnings.append("the assignment's grading mode is not \"manual\"")
    existing = service.db.get_assignment(classroom, assignment)
    candidate = Assignment(classroom=classroom, slug=assignment, template=body.template,
                           ref=body.ref,
                           hidden_tests=existing.hidden_tests if existing else None)
    try:
        template = ctx.repos.template(candidate)
        visible = visible_tests(template.dir, candidate, template.settings)
    except (GitHubError, FileNotFoundError, engine.TestFileError) as exc:
        raise HTTPException(422,
                            f"the template's tests could not be loaded: {exc}") from None
    max_points = (data.get("grading") or {}).get("max_points")
    total = sum(t.points for t in visible) + sum(t.points for t in parse_hidden(candidate))
    if max_points is not None and max_points != total:
        warnings.append(f"Classroom 50 expects {max_points} points but the tests add up to "
                        f"{total}")
    saved = service.db.upsert_assignment(classroom, assignment, body.template, body.ref,
                                         body.publish_release, body.save_score)
    return _out(saved, warnings)


@assignments_router.delete(ITEM, status_code=status.HTTP_204_NO_CONTENT,
                           summary="Stop grading an assignment",
                           description="Removes it from this service, with its hidden tests and "
                                       "watch state. Its grading history and the scores already "
                                       "in Classroom 50 are kept.")
def delete_assignment(request: Request, classroom: str, assignment: str) -> Response:
    if not request.app.state.service.db.delete_assignment(classroom, assignment):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assignment not configured")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@assignments_router.put(
    ITEM + "/watch", response_model=AssignmentOut, summary="Turn push watching on or off",
    description="With watching on, every push of every student is graded automatically, "
                "checking for new pushes every `interval_seconds`. With it off, submissions are "
                "only graded when asked through `POST /grade`.")
def set_watch(request: Request, classroom: str, assignment: str, body: WatchIn) -> AssignmentOut:
    a = request.app.state.service.db.set_watch(classroom, assignment, body.enabled,
                                               body.interval_seconds)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assignment not configured")
    return _out(a)


@assignments_router.post(ITEM + "/watch/run", summary="Check for new pushes now",
                         description="One watch round right now, even if watching is off. "
                                     "Returns how many submissions were queued.")
def run_watch(request: Request, classroom: str, assignment: str) -> dict:
    a = _get(request, classroom, assignment)
    return {"queued": request.app.state.service.watch_assignment(a)}


@assignments_router.get(ITEM + "/hidden-tests", response_model=HiddenTestsOut,
                        summary="Get the hidden tests")
def get_hidden_tests(request: Request, classroom: str, assignment: str) -> HiddenTestsOut:
    tests = _get(request, classroom, assignment).hidden_tests or []
    return HiddenTestsOut(tests=tests, points=sum(int(t.get("points", 0)) for t in tests))


@assignments_router.put(
    ITEM + "/hidden-tests", response_model=HiddenTestsOut, summary="Replace the hidden tests",
    description="Tests only the grader runs. Students run the visible ones (`.grader/tests.json`)"
                " with `check.py`; the grader runs the visible ones and these. Already graded "
                "submissions are graded again on the next request, because the tests changed.")
def put_hidden_tests(request: Request, classroom: str, assignment: str,
                     body: HiddenTestsIn) -> HiddenTestsOut:
    service = request.app.state.service
    a = _get(request, classroom, assignment)
    a.hidden_tests = body.tests
    try:
        hidden = parse_hidden(a)
        template = service.ctx.repos.template(a)
        visible = visible_tests(template.dir, a, template.settings)
    except (engine.TestFileError, GitHubError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from None
    repeated = {t.name for t in hidden} & {t.name for t in visible}
    if repeated:
        raise HTTPException(422,
                            f"names already used by visible tests: {sorted(repeated)}")
    service.db.set_hidden_tests(classroom, assignment, body.tests)
    return HiddenTestsOut(tests=body.tests, points=sum(t.points for t in hidden))


@assignments_router.delete(ITEM + "/hidden-tests", status_code=status.HTTP_204_NO_CONTENT,
                           summary="Remove the hidden tests")
def delete_hidden_tests(request: Request, classroom: str, assignment: str) -> Response:
    _get(request, classroom, assignment)
    request.app.state.service.db.set_hidden_tests(classroom, assignment, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@assignments_router.get(ITEM + "/students", response_model=list[StudentOut],
                        summary="Students who accepted the assignment",
                        description="From the student repositories in the organization, most "
                                    "recent push first.")
def list_students(request: Request, classroom: str, assignment: str) -> list[StudentOut]:
    a = _get(request, classroom, assignment)
    try:
        repos = request.app.state.service.ctx.repos.list_repos(a)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub: {exc.message}") from None
    return [StudentOut(user=u, repo=r, pushed_at=p or None) for u, r, p in repos]
