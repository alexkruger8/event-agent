import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.tenant import Tenants


class TenantKafkaSettings(Base):
    __tablename__ = "tenant_kafka_settings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="tenant_kafka_settings_pkey"),
        UniqueConstraint("tenant_id", name="tenant_kafka_settings_tenant_id_key"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            deferrable=True,
            name="tenant_kafka_settings_tenant_id_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    bootstrap_servers: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_include_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_exclude_pattern: Mapped[str] = mapped_column(Text, nullable=False, default="^__")
    error_topic_pattern: Mapped[str] = mapped_column(Text, nullable=False, default=r"\.errors?$")
    event_name_fields: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False,
        default=lambda: ["event_name", "type", "action", "name"],
    )
    security_protocol: Mapped[str | None] = mapped_column(Text, nullable=True)
    sasl_mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)
    sasl_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    sasl_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    sasl_password_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_connect_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_connect_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    messages_ingested_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)

    tenant: Mapped[Optional["Tenants"]] = relationship("Tenants", back_populates="kafka_settings")
