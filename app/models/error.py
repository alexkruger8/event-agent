import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
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


class Errors(Base):
    __tablename__ = "errors"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], deferrable=True, name="errors_tenant_id_fkey"),
        PrimaryKeyConstraint("id", name="errors_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(String)
    component: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="error")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    error_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)  # type: ignore[type-arg]
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="errors")
