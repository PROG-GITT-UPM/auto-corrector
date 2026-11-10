# external imports
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

# internal imports
from ..data_models.api import JobOut
from ..utilities.endpoints_tags import BASE_JOBS_ENDPOINT, JOBS_TAG
from ..utilities.security import require_token

jobs_router = APIRouter(prefix=BASE_JOBS_ENDPOINT, tags=[JOBS_TAG],
                        dependencies=[Depends(require_token)])


def _out(row: dict) -> JobOut:
    summary = row.get("summary") or {}
    return JobOut(
        id=row["id"], classroom=row["classroom"], assignment=row["slug"], user=row["username"],
        source=row["source"], status=row["status"], force=row["force"], until=row["until"],
        created_at=row["created_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], outcome=row["outcome"], commit=row["commit"],
        points=row["points"], max_points=row["max_points"], late=row["late"],
        release_url=row["release_url"], tests=summary.get("tests"), error=row["error"])


@jobs_router.get("", response_model=list[JobOut], summary="Grading jobs, newest first")
def list_jobs(request: Request, classroom: Optional[str] = None,
              assignment: Optional[str] = None,
              user: Optional[str] = Query(None, description="GitHub username"),
              status_: Optional[Literal["queued", "grading", "done", "error"]] = Query(
                  None, alias="status"),
              limit: int = Query(100, ge=1, le=1000)) -> list[JobOut]:
    rows = request.app.state.service.db.list_jobs(classroom, assignment, user, status_, limit)
    return [_out(r) for r in rows]


@jobs_router.get("/{job_id}", response_model=JobOut, summary="One grading job")
def get_job(request: Request, job_id: str) -> JobOut:
    row = request.app.state.service.db.get_job(job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return _out(row)
