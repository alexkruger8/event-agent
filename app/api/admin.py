from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.workers import metric_worker

router = APIRouter(prefix="/admin", tags=["admin"])


class WorkerResult(BaseModel):
    status: str


@router.post("/run-pipeline", response_model=WorkerResult)
def run_pipeline(db: Session = Depends(get_db)) -> WorkerResult:
    """Trigger the full metrics pipeline for all tenants."""
    metric_worker.run(db)
    return WorkerResult(status="ok")
