import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Double,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.anomaly import Anomalies
    from app.models.conversation import Conversations
    from app.models.notification import Notifications
    from app.models.tenant import Tenants
    from app.models.trend import Trends


class Insights(Base):
    __tablename__ = "insights"
    __table_args__ = (
        ForeignKeyConstraint(["anomaly_id"], ["anomalies.id"], deferrable=True, name="insights_anomaly_id_fkey"),
        ForeignKeyConstraint(["trend_id"], ["trends.id"], deferrable=True, name="insights_trend_id_fkey"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="insights_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="insights_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    anomaly_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    trend_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Double(53))
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    anomaly: Mapped[Optional["Anomalies"]] = relationship("Anomalies", back_populates="insights")
    trend: Mapped[Optional["Trends"]] = relationship("Trends", back_populates="insights")
    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="insights")
    conversations: Mapped[list["Conversations"]] = relationship("Conversations", back_populates="insight")
    notifications: Mapped[list["Notifications"]] = relationship("Notifications", back_populates="insight")
