import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class EventIngest(BaseModel):
    event_name: str
    user_id: str | None = None
    timestamp: datetime.datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class EventResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    event_name: str
    user_id: str | None
    timestamp: datetime.datetime
    properties: dict[str, Any]
    ingested_at: datetime.datetime

    model_config = {"from_attributes": True}


class BatchEventIngest(BaseModel):
    events: list[EventIngest] = Field(..., min_length=1, max_length=1000)


class BatchEventResponse(BaseModel):
    accepted: int
