"""Shared event ingestion logic used by both the REST API and the Kafka consumer."""

import datetime
import uuid

from sqlalchemy.orm import Session

from app.models.event import Events, EventTypes


def ingest_event(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    user_id: str | None,
    timestamp: datetime.datetime,
    properties: dict,  # type: ignore[type-arg]
    ingested_at: datetime.datetime,
) -> Events:
    event = Events(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        user_id=user_id,
        timestamp=timestamp,
        properties=properties,
        ingested_at=ingested_at,
    )
    db.add(event)
    _upsert_event_type(db, tenant_id, event_name, timestamp)
    return event


def _upsert_event_type(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    timestamp: datetime.datetime,
) -> None:
    event_type = db.query(EventTypes).filter(
        EventTypes.tenant_id == tenant_id,
        EventTypes.event_name == event_name,
    ).first()

    if event_type is None:
        db.add(EventTypes(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_name=event_name,
            first_seen=timestamp,
            last_seen=timestamp,
            total_events=1,
        ))
    else:
        existing_last = event_type.last_seen
        if existing_last is not None and existing_last.tzinfo is None:
            existing_last = existing_last.replace(tzinfo=datetime.UTC)
        ts_aware = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=datetime.UTC)
        event_type.last_seen = max(ts_aware, existing_last or ts_aware)
        event_type.total_events = (event_type.total_events or 0) + 1
