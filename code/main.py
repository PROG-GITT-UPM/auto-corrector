# external imports
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

# internal imports
from src.config.settings import Settings
from src.modules.grading import Context
from src.modules.service import GradingService
from src.providers.database import Database
# Routers imports
from src.routers import *
from src.utilities.constants import SERVICE_NAME, VERSION
from src.utilities.endpoints_tags import TAGS_METADATA

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(SERVICE_NAME)

DESCRIPTION = """
Grades the course's Python assignments on our own servers and records the scores in
Classroom 50, without GitHub Actions.

1. Bring in the assignments declared in Classroom 50 with `POST /assignments/sync`, or add one
   by hand with `PUT /assignments/{classroom}/{assignment}`.
2. Grade on request with `POST /grade`, or turn on push watching (off by default) with
   `PUT /assignments/{classroom}/{assignment}/watch` to grade every push.
3. Follow the jobs with `GET /jobs`. Scores reach Classroom 50 in batches.

Every endpoint except `/` and `/health` needs the API token: press **Authorize**.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = getattr(app.state, "settings", None) or Settings.from_env()
    app.state.settings = settings
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    version = db.migrate()
    log.info("database schema at version %d", version)
    service = GradingService(Context(settings, db))
    app.state.service = service
    if getattr(app.state, "start_background", True):
        service.start()
    log.info("%s %s ready", SERVICE_NAME, VERSION)
    yield
    log.info("shutting down: finishing and saving pending scores")
    service.stop()
    db.close()


def create_app() -> FastAPI:
    # needs to be created outside __main__ to be found by the executing command
    app = FastAPI(title=SERVICE_NAME, version=VERSION, description=DESCRIPTION,
                  openapi_tags=TAGS_METADATA, lifespan=lifespan)
    # Routers inclusion
    app.include_router(router=default_router)
    app.include_router(router=assignments_router)
    app.include_router(router=grading_router)
    app.include_router(router=jobs_router)
    app.include_router(router=scores_router)
    return app


app = create_app()
