import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.error import Errors
from app.schemas.error import (
    BatchErrorIngest,
    BatchErrorResponse,
    ErrorDetailResponse,
    ErrorIngest,
    ErrorResponse,
)
from app.services.error_ingestion import upsert_error as _upsert_error

router = APIRouter(prefix="/errors", tags=["errors"])


@router.post("/{tenant_id}", response_model=ErrorResponse, status_code=status.HTTP_201_CREATED)
def ingest_error(
    tenant_id: uuid.UUID,
    payload: ErrorIngest,
    db: Session = Depends(get_db),
) -> Errors:
    now = datetime.datetime.now(datetime.UTC)
    error, _ = _upsert_error(
        db,
        tenant_id=tenant_id,
        error_type=payload.error_type,
        message=payload.message,
        stack_trace=payload.stack_trace,
        service=payload.service,
        component=payload.component,
        severity=payload.severity,
        fingerprint=payload.fingerprint,
        error_metadata=payload.metadata or None,
        now=now,
    )
    db.flush()
    db.refresh(error)
    return error


@router.post("/{tenant_id}/batch", response_model=BatchErrorResponse, status_code=status.HTTP_201_CREATED)
def ingest_errors_batch(
    tenant_id: uuid.UUID,
    payload: BatchErrorIngest,
    db: Session = Depends(get_db),
) -> BatchErrorResponse:
    now = datetime.datetime.now(datetime.UTC)
    upserted = 0
    for item in payload.errors:
        _, was_upserted = _upsert_error(
            db,
            tenant_id=tenant_id,
            error_type=item.error_type,
            message=item.message,
            stack_trace=item.stack_trace,
            service=item.service,
            component=item.component,
            severity=item.severity,
            fingerprint=item.fingerprint,
            error_metadata=item.metadata or None,
            now=now,
        )
        if was_upserted:
            upserted += 1
    return BatchErrorResponse(accepted=len(payload.errors), upserted=upserted)


@router.get("/{tenant_id}", response_model=list[ErrorResponse])
def list_errors(
    tenant_id: uuid.UUID,
    severity: str | None = None,
    service: str | None = None,
    resolved: bool | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Errors]:
    q = db.query(Errors).filter(Errors.tenant_id == tenant_id)
    if severity is not None:
        q = q.filter(Errors.severity == severity)
    if service is not None:
        q = q.filter(Errors.service == service)
    if resolved is True:
        q = q.filter(Errors.resolved_at.is_not(None))
    elif resolved is False:
        q = q.filter(Errors.resolved_at.is_(None))
    if since is not None:
        q = q.filter(Errors.last_seen_at >= since)
    if until is not None:
        q = q.filter(Errors.last_seen_at <= until)
    return q.order_by(Errors.last_seen_at.desc()).limit(limit).offset(offset).all()


@router.get("/{tenant_id}/{error_id}", response_model=ErrorDetailResponse)
def get_error(
    tenant_id: uuid.UUID,
    error_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Errors:
    error = db.query(Errors).filter(Errors.tenant_id == tenant_id, Errors.id == error_id).first()
    if error is None:
        raise HTTPException(status_code=404, detail="Error not found")
    return error


@router.patch("/{tenant_id}/{error_id}/resolve", response_model=ErrorResponse)
def resolve_error(
    tenant_id: uuid.UUID,
    error_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Errors:
    error = db.query(Errors).filter(Errors.tenant_id == tenant_id, Errors.id == error_id).first()
    if error is None:
        raise HTTPException(status_code=404, detail="Error not found")
    if error.resolved_at is not None:
        raise HTTPException(status_code=409, detail="Error is already resolved")
    error.resolved_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    db.refresh(error)
    return error
