import datetime
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.event import Events, EventTypes
from app.schemas.event import BatchEventIngest, BatchEventResponse, EventIngest, EventResponse
from app.services.event_ingestion import ingest_event as _ingest_event

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/{tenant_id}", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def ingest_event(
    tenant_id: uuid.UUID,
    payload: EventIngest,
    db: Session = Depends(get_db),
) -> Events:
    now = datetime.datetime.now(datetime.UTC)
    event = _ingest_event(
        db,
        tenant_id=tenant_id,
        event_name=payload.event_name,
        user_id=payload.user_id,
        timestamp=payload.timestamp or now,
        properties=payload.properties,
        ingested_at=now,
    )
    db.flush()
    db.refresh(event)
    return event


@router.post("/{tenant_id}/batch", response_model=BatchEventResponse, status_code=status.HTTP_201_CREATED)
def ingest_events_batch(
    tenant_id: uuid.UUID,
    payload: BatchEventIngest,
    db: Session = Depends(get_db),
) -> BatchEventResponse:
    now = datetime.datetime.now(datetime.UTC)

    # Build all event rows
    event_rows = [
        Events(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_name=e.event_name,
            user_id=e.user_id,
            timestamp=e.timestamp or now,
            properties=e.properties,
            ingested_at=now,
        )
        for e in payload.events
    ]
    db.add_all(event_rows)

    # Upsert event_types — one query per unique event name in the batch
    counts: dict[str, int] = {}
    earliest: dict[str, datetime.datetime] = {}
    latest: dict[str, datetime.datetime] = {}
    for row in event_rows:
        name = row.event_name or ""
        ts = row.timestamp if isinstance(row.timestamp, datetime.datetime) else now
        counts[name] = counts.get(name, 0) + 1
        if name not in earliest or ts < earliest[name]:
            earliest[name] = ts
        if name not in latest or ts > latest[name]:
            latest[name] = ts

    existing = {
        et.event_name: et
        for et in db.query(EventTypes).filter(
            EventTypes.tenant_id == tenant_id,
            EventTypes.event_name.in_(counts.keys()),
        ).all()
        if et.event_name is not None
    }

    for name, count in counts.items():
        if name in existing:
            et = existing[name]
            et.total_events = (et.total_events or 0) + count
            et.last_seen = max(latest[name], et.last_seen or latest[name])
        else:
            db.add(EventTypes(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                event_name=name,
                first_seen=earliest[name],
                last_seen=latest[name],
                total_events=count,
            ))

    return BatchEventResponse(accepted=len(event_rows))
