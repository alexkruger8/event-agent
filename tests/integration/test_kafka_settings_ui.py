"""Integration tests for tenant Kafka settings."""
import datetime
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.tenant import Tenants
from app.models.tenant_kafka_settings import TenantKafkaSettings
from app.security.encryption import decrypt_secret


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _post_settings(
    client: TestClient,
    tenant_id: uuid.UUID,
    **overrides: str,
) -> None:
    payload = {
        "bootstrap_servers": "broker.example.com:9092",
        "topic_include_pattern": r"^app\.",
        "topic_exclude_pattern": "^__",
        "error_topic_pattern": r"\.errors?$",
        "event_name_fields": "event_name, type, action, name",
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_username": "test-user",
        "sasl_password": "",
        "enabled": "on",
        **overrides,
    }
    response = client.post(f"/ui/tenants/{tenant_id}/kafka", data=payload)
    assert response.status_code == 200, response.text
    assert response.text == "Saved."


@pytest.mark.integration
def test_create_tenant_kafka_settings(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _post_settings(client, tenant_id)

    settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .one()
    )
    assert settings.bootstrap_servers == "broker.example.com:9092"
    assert settings.topic_include_pattern == r"^app\."
    assert settings.topic_exclude_pattern == "^__"
    assert settings.error_topic_pattern == r"\.errors?$"
    assert settings.event_name_fields == ["event_name", "type", "action", "name"]
    assert settings.security_protocol == "SASL_SSL"
    assert settings.sasl_mechanism == "SCRAM-SHA-256"
    assert settings.sasl_username == "test-user"
    assert settings.enabled is True


@pytest.mark.integration
def test_updates_existing_tenant_kafka_settings(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _post_settings(client, tenant_id, bootstrap_servers="first.example.com:9092")
    _post_settings(
        client,
        tenant_id,
        bootstrap_servers="second.example.com:9092",
        topic_include_pattern="",
        event_name_fields="name, action",
    )

    settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .all()
    )
    assert len(settings) == 1
    assert settings[0].bootstrap_servers == "second.example.com:9092"
    assert settings[0].topic_include_pattern is None
    assert settings[0].event_name_fields == ["name", "action"]


@pytest.mark.integration
def test_saves_encrypted_sasl_password(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    key = Fernet.generate_key().decode("ascii")

    with patch("app.security.encryption.settings") as mock_settings:
        mock_settings.kafka_credential_encryption_key = key
        _post_settings(client, tenant_id, sasl_password="super-secret")

    settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .one()
    )
    assert settings.sasl_password_encrypted is not None
    assert settings.sasl_password_encrypted != "super-secret"
    assert settings.sasl_password_updated_at is not None

    with patch("app.security.encryption.settings") as mock_settings:
        mock_settings.kafka_credential_encryption_key = key
        assert decrypt_secret(settings.sasl_password_encrypted) == "super-secret"


@pytest.mark.integration
def test_blank_password_preserves_existing_password(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    key = Fernet.generate_key().decode("ascii")

    with patch("app.security.encryption.settings") as mock_settings:
        mock_settings.kafka_credential_encryption_key = key
        _post_settings(client, tenant_id, sasl_password="super-secret")

    settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .one()
    )
    original_encrypted = settings.sasl_password_encrypted
    original_updated_at = settings.sasl_password_updated_at

    _post_settings(client, tenant_id, bootstrap_servers="updated.example.com:9092", sasl_password="")

    assert settings.bootstrap_servers == "updated.example.com:9092"
    assert settings.sasl_password_encrypted == original_encrypted
    assert settings.sasl_password_updated_at == original_updated_at


@pytest.mark.integration
def test_clear_sasl_password(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    key = Fernet.generate_key().decode("ascii")

    with patch("app.security.encryption.settings") as mock_settings:
        mock_settings.kafka_credential_encryption_key = key
        _post_settings(client, tenant_id, sasl_password="super-secret")

    _post_settings(client, tenant_id, clear_sasl_password="on")

    settings = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == tenant_id)
        .one()
    )
    assert settings.sasl_password_encrypted is None
    assert settings.sasl_password_updated_at is None
