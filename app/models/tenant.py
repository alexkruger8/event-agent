import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, PrimaryKeyConstraint, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.anomaly import Anomalies
    from app.models.conversation import Conversations
    from app.models.error import Errors
    from app.models.event import Events, EventTypes
    from app.models.insight import Insights
    from app.models.metric import MetricBaselines, Metrics
    from app.models.notification import Notifications
    from app.models.tenant_kafka_settings import TenantKafkaSettings
    from app.models.trend import Trends


class Tenants(Base):
    __tablename__ = "tenants"
    __table_args__ = (PrimaryKeyConstraint("id", name="tenants_pkey"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    slack_channel: Mapped[str | None] = mapped_column(String)
    sms_recipients: Mapped[list | None] = mapped_column(JSONB)  # type: ignore[type-arg]

    errors: Mapped[list["Errors"]] = relationship("Errors", back_populates="tenant")
    kafka_settings: Mapped[Optional["TenantKafkaSettings"]] = relationship("TenantKafkaSettings", back_populates="tenant", uselist=False)
    event_types: Mapped[list["EventTypes"]] = relationship("EventTypes", back_populates="tenant")
    events: Mapped[list["Events"]] = relationship("Events", back_populates="tenant")
    metric_baselines: Mapped[list["MetricBaselines"]] = relationship("MetricBaselines", back_populates="tenant")
    metrics: Mapped[list["Metrics"]] = relationship("Metrics", back_populates="tenant")
    anomalies: Mapped[list["Anomalies"]] = relationship("Anomalies", back_populates="tenant")
    insights: Mapped[list["Insights"]] = relationship("Insights", back_populates="tenant")
    trends: Mapped[list["Trends"]] = relationship("Trends", back_populates="tenant")
    conversations: Mapped[list["Conversations"]] = relationship("Conversations", back_populates="tenant")
    notifications: Mapped[list["Notifications"]] = relationship("Notifications", back_populates="tenant")
