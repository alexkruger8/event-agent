import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.insight import Insights
    from app.models.tenant import Tenants


class Conversations(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(["insight_id"], ["insights.id"], deferrable=True, name="conversations_insight_id_fkey"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="conversations_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="conversations_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    insight_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    channel: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    insight: Mapped[Optional["Insights"]] = relationship("Insights", back_populates="conversations")
    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="conversations")
    messages: Mapped[list["Messages"]] = relationship("Messages", back_populates="conversation")


class Messages(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], deferrable=True, name="messages_conversation_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="messages_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    sender: Mapped[str | None] = mapped_column(String)
    message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)  # type: ignore[type-arg]
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    conversation: Mapped[Optional["Conversations"]] = relationship("Conversations", back_populates="messages")
