import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.error import Errors


def get_recent_errors(
    db: Session,
    tenant_id: str,
    service: str | None,
    severity: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Return recent unresolved errors, optionally filtered by service and severity."""
    tid = uuid.UUID(tenant_id)

    q = db.query(Errors).filter(
        Errors.tenant_id == tid,
        Errors.resolved_at.is_(None),
    )
    if service is not None:
        q = q.filter(Errors.service == service)
    if severity is not None:
        q = q.filter(Errors.severity == severity)

    rows = q.order_by(Errors.last_seen_at.desc()).limit(limit).all()
    return [_error_dict(e) for e in rows]


def get_unresolved_errors(
    db: Session,
    tenant_id: str,
    service: str | None,
    min_occurrences: int,
) -> list[dict[str, Any]]:
    """Return unresolved errors sorted by occurrence count (noisiest first)."""
    tid = uuid.UUID(tenant_id)

    q = db.query(Errors).filter(
        Errors.tenant_id == tid,
        Errors.resolved_at.is_(None),
        Errors.occurrence_count >= min_occurrences,
    )
    if service is not None:
        q = q.filter(Errors.service == service)

    rows = q.order_by(Errors.occurrence_count.desc()).limit(50).all()
    return [_error_dict(e) for e in rows]


def _error_dict(e: Errors) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "error_type": e.error_type,
        "message": e.message,
        "service": e.service,
        "component": e.component,
        "severity": e.severity,
        "occurrence_count": e.occurrence_count,
        "first_seen_at": e.first_seen_at.isoformat(),
        "last_seen_at": e.last_seen_at.isoformat(),
        "resolved": e.resolved_at is not None,
    }
