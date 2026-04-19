"""Shared error ingestion logic used by both the REST API and the Kafka consumer."""

import datetime
import hashlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.error import Errors

_RESERVED_FIELDS = {"error_type", "type", "message", "msg", "stack_trace", "stacktrace", "stack",
                    "service", "component", "severity", "fingerprint", "timestamp", "tenant_id"}


def compute_fingerprint(error_type: str, message: str, service: str | None) -> str:
    raw = f"{error_type}:{message}:{service or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def upsert_error(
    db: Session,
    tenant_id: uuid.UUID,
    error_type: str,
    message: str,
    stack_trace: str | None,
    service: str | None,
    component: str | None,
    severity: str,
    fingerprint: str | None,
    error_metadata: dict[str, Any] | None,
    now: datetime.datetime,
) -> tuple[Errors, bool]:
    """Insert or update an error row. Returns (row, was_upserted)."""
    fp = fingerprint or compute_fingerprint(error_type, message, service)

    existing = (
        db.query(Errors)
        .filter(
            Errors.tenant_id == tenant_id,
            Errors.fingerprint == fp,
            Errors.resolved_at.is_(None),
        )
        .first()
    )

    if existing is not None:
        existing.occurrence_count = existing.occurrence_count + 1
        existing.last_seen_at = now
        return existing, True

    error = Errors(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        error_type=error_type,
        message=message,
        stack_trace=stack_trace,
        service=service,
        component=component,
        severity=severity,
        fingerprint=fp,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        error_metadata=error_metadata or None,
        ingested_at=now,
    )
    db.add(error)
    return error, False
