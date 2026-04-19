import datetime
import hashlib
import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

VALID_SEVERITIES = {"debug", "info", "warning", "error", "critical"}


class ErrorIngest(BaseModel):
    error_type: str
    message: str
    stack_trace: str | None = None
    service: str | None = None
    component: str | None = None
    severity: str = "error"
    fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def compute_fingerprint(self) -> "ErrorIngest":
        if self.fingerprint is None:
            raw = f"{self.error_type}:{self.message}:{self.service or ''}"
            self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {VALID_SEVERITIES}")
        return self


class ErrorResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    error_type: str
    message: str
    service: str | None
    component: str | None
    severity: str
    fingerprint: str | None
    occurrence_count: int
    first_seen_at: datetime.datetime
    last_seen_at: datetime.datetime
    resolved_at: datetime.datetime | None

    model_config = {"from_attributes": True}


class ErrorDetailResponse(ErrorResponse):
    stack_trace: str | None
    error_metadata: dict[str, Any] | None
    ingested_at: datetime.datetime


class BatchErrorIngest(BaseModel):
    errors: list[ErrorIngest] = Field(..., min_length=1, max_length=1000)


class BatchErrorResponse(BaseModel):
    accepted: int
    upserted: int
