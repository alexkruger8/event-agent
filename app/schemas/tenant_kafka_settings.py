import uuid

from pydantic import BaseModel


class TenantKafkaSettingsUpdate(BaseModel):
    bootstrap_servers: str | None = None
    topic_include_pattern: str | None = None
    topic_exclude_pattern: str = "^__"
    error_topic_pattern: str = r"\.errors?$"
    event_name_fields: list[str] = ["event_name", "type", "action", "name"]
    enabled: bool = True


class TenantKafkaSettingsResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    bootstrap_servers: str | None
    topic_include_pattern: str | None
    topic_exclude_pattern: str
    error_topic_pattern: str
    event_name_fields: list[str]
    enabled: bool

    model_config = {"from_attributes": True}
