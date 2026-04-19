import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.tenant import Tenants


class EventTypes(Base):
    __tablename__ = "event_types"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="event_types_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="event_types_pkey"),
        Index("event_types_tenant_id_event_name_idx", "tenant_id", "event_name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    event_name: Mapped[str | None] = mapped_column(String)
    first_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    total_events: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    type_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)  # type: ignore[type-arg]

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="event_types")


class Events(Base):
    __tablename__ = "events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="events_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="events_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    event_name: Mapped[str | None] = mapped_column(String)
    user_id: Mapped[str | None] = mapped_column(String)
    timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    properties: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]
    ingested_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="events")
