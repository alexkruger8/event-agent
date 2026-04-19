import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Double,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.insight import Insights
    from app.models.tenant import Tenants


class Trends(Base):
    __tablename__ = "trends"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="trends_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="trends_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metric_name: Mapped[str | None] = mapped_column(String)
    direction: Mapped[str | None] = mapped_column(String)       # "up" | "down"
    slope_per_hour: Mapped[float | None] = mapped_column(Double(53))
    change_percent_per_hour: Mapped[float | None] = mapped_column(Double(53))
    window_start: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    window_end: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    mean_value: Mapped[float | None] = mapped_column(Double(53))
    detected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    context: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="trends")
    insights: Mapped[list["Insights"]] = relationship("Insights", back_populates="trend")
