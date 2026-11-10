# external imports
from fastapi import APIRouter, Depends, HTTPException, Request, status

# internal imports
from ..data_models.api import GradeRequest, GradeResponse
from ..providers.github import GitHubError
from ..utilities.endpoints_tags import BASE_GRADING_ENDPOINT, GRADING_TAG
from ..utilities.security import require_token

grading_router = APIRouter(prefix=BASE_GRADING_ENDPOINT, tags=[GRADING_TAG],
                           dependencies=[Depends(require_token)])


@grading_router.post(
    "", response_model=GradeResponse, status_code=status.HTTP_202_ACCEPTED,
    summary="Grade submissions",
    description="Queues one job per student and returns at once; follow them with `GET /jobs`. "
                "A commit already graded with the same tests is not graded again (the job ends "
                "as `already graded`) unless `force` is set. Scores are written to Classroom 50 "
                "in batches; `POST /scores/save` writes them right away.")
def grade(request: Request, body: GradeRequest) -> GradeResponse:
    service = request.app.state.service
    assignment = service.db.get_assignment(body.classroom, body.assignment)
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"assignment {body.classroom}/{body.assignment} is not configured: "
                            "add it with PUT /assignments/{classroom}/{assignment}")
    students = body.students
    if not students:
        try:
            students = [u for u, _, _ in service.ctx.repos.list_repos(assignment)]
        except GitHubError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub: {exc.message}") from None
    until = body.until.strftime("%Y-%m-%dT%H:%M:%SZ") if body.until else None
    jobs, already = [], []
    for user in students:
        ident = service.enqueue(assignment, user, force=body.force, until=until)
        (jobs.append(ident) if ident else already.append(user))
    return GradeResponse(jobs=jobs, students=len(students), already_queued=already)
