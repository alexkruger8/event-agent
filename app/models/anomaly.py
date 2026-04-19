import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Double, ForeignKeyConstraint, PrimaryKeyConstraint, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.insight import Insights
    from app.models.metric import Metrics
    from app.models.tenant import Tenants


class Anomalies(Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        ForeignKeyConstraint(["metric_id"], ["metrics.id"], deferrable=True, name="anomalies_metric_id_fkey"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="anomalies_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="anomalies_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metric_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metric_name: Mapped[str | None] = mapped_column(String)
    metric_timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    current_value: Mapped[float | None] = mapped_column(Double(53))
    baseline_value: Mapped[float | None] = mapped_column(Double(53))
    deviation_percent: Mapped[float | None] = mapped_column(Double(53))
    severity: Mapped[str | None] = mapped_column(String)
    detected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    context: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]

    metric: Mapped[Optional["Metrics"]] = relationship("Metrics", back_populates="anomalies")
    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="anomalies")
    insights: Mapped[list["Insights"]] = relationship("Insights", back_populates="anomaly")
