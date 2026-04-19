import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.api import admin, analytics, errors, events, slack_events, sms_events, ui
from app.config import settings
from app.database.migrations import ensure_runtime_schema
from app.database.session import _get_session_local, get_db
from app.middleware.auth import AuthMiddleware
from app.workers import metric_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


def _run_pipeline() -> None:
    """Scheduled pipeline job — runs for all tenants, manages its own DB session."""
    db = _get_session_local()()
    try:
        metric_worker.run(db)
    except Exception:
        logger.exception("Scheduled pipeline run failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    ensure_runtime_schema()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_pipeline,
        "interval",
        minutes=settings.pipeline_schedule_minutes,
    )
    scheduler.start()
    logger.info("Pipeline scheduler started (every %d min)", settings.pipeline_schedule_minutes)
    yield
    scheduler.shutdown(wait=False)
    logger.info("Pipeline scheduler stopped")


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)

app.include_router(errors.router)
app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(slack_events.router)
app.include_router(sms_events.router)
app.include_router(ui.router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    db = next(get_db())
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")
