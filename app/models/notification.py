import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.insight import Insights
    from app.models.tenant import Tenants


class Notifications(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(["insight_id"], ["insights.id"], deferrable=True, name="notifications_insight_id_fkey"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="notifications_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="notifications_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    insight_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    channel: Mapped[str | None] = mapped_column(String)
    external_message_id: Mapped[str | None] = mapped_column(String)
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    insight: Mapped[Optional["Insights"]] = relationship("Insights", back_populates="notifications")
    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="notifications")
