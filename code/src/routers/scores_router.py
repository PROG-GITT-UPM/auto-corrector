# external imports
from fastapi import APIRouter, Depends, HTTPException, Request, status

# internal imports
from ..data_models.api import PendingScoreOut, SavedScoreOut
from ..providers.github import GitHubError
from ..utilities.endpoints_tags import BASE_SCORES_ENDPOINT, SCORES_TAG
from ..utilities.security import require_token

scores_router = APIRouter(prefix=BASE_SCORES_ENDPOINT, tags=[SCORES_TAG],
                          dependencies=[Depends(require_token)])


@scores_router.get("/pending", response_model=list[PendingScoreOut],
                   summary="Scores not yet written to Classroom 50")
def pending(request: Request) -> list[PendingScoreOut]:
    rows = request.app.state.service.db.pending_scores()
    return [PendingScoreOut(classroom=r["classroom"], assignment=r["slug"], user=r["owner"],
                            score=r["record"]["score"], max_score=r["record"]["max-score"],
                            graded_at=r["record"]["graded_at"], queued_at=r["created_at"])
            for r in rows]


@scores_router.post("/save", response_model=list[SavedScoreOut],
                    summary="Write the pending scores to Classroom 50 now",
                    description="One commit per classroom, with `[skip ci]` so that it does not "
                                "trigger Classroom 50's publish-pages workflow.")
def save(request: Request) -> list[SavedScoreOut]:
    try:
        statuses = request.app.state.service.save_scores()
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"some scores could not be written and stay pending: {exc.message}"
                            ) from None
    return [SavedScoreOut(classroom=c, assignment=a, user=u, result=r)
            for (c, a, u), r in statuses.items()]
