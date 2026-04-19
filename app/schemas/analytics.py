import datetime
import uuid
from typing import Any

from pydantic import BaseModel


class InsightSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    summary: str | None
    confidence: float | None
    created_at: datetime.datetime | None

    model_config = {"from_attributes": True}


class InsightDetail(InsightSummary):
    explanation: str | None


class AnomalyResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    metric_name: str | None
    metric_timestamp: datetime.datetime | None
    current_value: float | None
    baseline_value: float | None
    deviation_percent: float | None
    severity: str | None
    detected_at: datetime.datetime | None
    acknowledged_at: datetime.datetime | None
    resolved_at: datetime.datetime | None
    context: dict[str, Any] | None
    insight: InsightSummary | None = None

    model_config = {"from_attributes": True}


class AnomalyDetailResponse(AnomalyResponse):
    insight: InsightDetail | None = None


class MetricResponse(BaseModel):
    id: uuid.UUID
    metric_name: str | None
    metric_timestamp: datetime.datetime | None
    value: float | None
    tags: dict[str, Any] | None

    model_config = {"from_attributes": True}


class TrendResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    metric_name: str | None
    direction: str | None
    slope_per_hour: float | None
    change_percent_per_hour: float | None
    window_start: datetime.datetime | None
    window_end: datetime.datetime | None
    sample_size: int | None
    mean_value: float | None
    detected_at: datetime.datetime | None
    resolved_at: datetime.datetime | None
    context: dict[str, Any] | None

    model_config = {"from_attributes": True}


class InsightResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    title: str | None
    summary: str | None
    explanation: str | None
    confidence: float | None
    created_at: datetime.datetime | None
    anomaly: AnomalyResponse | None = None

    model_config = {"from_attributes": True}
