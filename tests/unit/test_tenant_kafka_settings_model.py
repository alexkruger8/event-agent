import uuid

import pytest
from sqlalchemy import Uuid

from app.models.tenant_kafka_settings import TenantKafkaSettings


@pytest.mark.unit
def test_tenant_kafka_settings_uuid_columns_use_native_uuid_type() -> None:
    assert isinstance(TenantKafkaSettings.__table__.c.id.type, Uuid)
    assert isinstance(TenantKafkaSettings.__table__.c.tenant_id.type, Uuid)


@pytest.mark.unit
def test_tenant_kafka_settings_accepts_uuid_values() -> None:
    tenant_id = uuid.uuid4()
    settings = TenantKafkaSettings(id=uuid.uuid4(), tenant_id=tenant_id)

    assert settings.tenant_id == tenant_id

