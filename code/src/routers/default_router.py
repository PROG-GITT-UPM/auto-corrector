# external imports
from fastapi import APIRouter, Request

# internal imports
from ..data_models.api import HealthOut
from ..providers.sandbox import image_available
from ..utilities.constants import SERVICE_NAME, VERSION
from ..utilities.endpoints_tags import DEFAULT_ENDPOINT, DEFAULT_TAG, HEALTH_ENDPOINT

default_router = APIRouter(tags=[DEFAULT_TAG])


@default_router.get(DEFAULT_ENDPOINT, summary="Service information")
def root() -> dict:
    return {"service": SERVICE_NAME, "version": VERSION, "docs": "/docs"}


@default_router.get(HEALTH_ENDPOINT, response_model=HealthOut, summary="Service health",
                    description="Does not need the API token.")
def health(request: Request) -> HealthOut:
    service = request.app.state.service
    settings = request.app.state.settings
    db = service.db
    database = db.ping()
    image = (image_available(settings.sandbox["image"])
             if settings.sandbox.get("type") == "docker" else True)
    counts = db.queue_counts() if database else {"queued": 0, "grading": 0}
    return HealthOut(
        status="ok" if database and image else "degraded",
        database=database, sandbox_image=image,
        queued=counts["queued"], grading=counts["grading"],
        pending_scores=db.pending_count() if database else 0,
        watched_assignments=(sum(a.watch_enabled for a in db.list_assignments())
                             if database else 0),
    )
