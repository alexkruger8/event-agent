from __future__ import annotations

from sqlalchemy import text

from app.database.engine import get_engine


def ensure_runtime_schema() -> None:
    """Apply small idempotent schema upgrades for local/dev deployments."""
    statements = [
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS security_protocol TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS sasl_mechanism TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS sasl_username TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS sasl_password_encrypted TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS sasl_password_updated_at TIMESTAMP WITHOUT TIME ZONE
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS last_connect_at TIMESTAMP WITHOUT TIME ZONE
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS last_connect_error TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP WITHOUT TIME ZONE
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS last_message_topic TEXT
        """,
        """
        ALTER TABLE tenant_kafka_settings
          ADD COLUMN IF NOT EXISTS messages_ingested_count BIGINT NOT NULL DEFAULT 0
        """,
    ]
    engine = get_engine()
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
