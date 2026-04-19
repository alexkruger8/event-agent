import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Double,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.anomaly import Anomalies
    from app.models.tenant import Tenants


class MetricBaselines(Base):
    __tablename__ = "metric_baselines"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="metric_baselines_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="metric_baselines_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metric_name: Mapped[str | None] = mapped_column(String)
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger)
    hour_of_day: Mapped[int | None] = mapped_column(SmallInteger)
    tags: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]
    avg_value: Mapped[float | None] = mapped_column(Double(53))
    stddev: Mapped[float | None] = mapped_column(Double(53))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="metric_baselines")


class Metrics(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="metrics_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="metrics_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metric_name: Mapped[str | None] = mapped_column(String)
    metric_timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    value: Mapped[float | None] = mapped_column(Double(53))
    tags: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="metrics")
    anomalies: Mapped[list["Anomalies"]] = relationship("Anomalies", back_populates="metric")
