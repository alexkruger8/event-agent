"""Unit tests for Kafka consumer message parsing — no database or broker required."""
import datetime
import re
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.workers.kafka_consumer import (
    _compile_route,
    _consumer_config,
    _consumer_key,
    _extract_event_name,
    _ingest_error_message,
    _ingest_event_message,
    _matches_tenant,
    _subscription_pattern,
    _TenantRoute,
)

NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
TENANT_ID = uuid.uuid4()


def _route(
    event_name_fields: list[str] | None = None,
    include: str | None = None,
    exclude: str | None = "^__",
    error: str = r"\.errors?$",
) -> _TenantRoute:
    return _TenantRoute(
        tenant_id=TENANT_ID,
        bootstrap_servers="broker:9092",
        include_re=re.compile(include) if include else None,
        exclude_re=re.compile(exclude) if exclude else None,
        error_re=re.compile(error),
        event_name_fields=event_name_fields or ["event_name", "type", "action", "name"],
    )


@pytest.mark.unit
def test_consumer_key_changes_with_tenant_specific_connection() -> None:
    route_a = _route()
    route_b = _route()
    route_b.sasl_username = "other-user"

    assert _consumer_key(route_a) != _consumer_key(route_b)


@pytest.mark.unit
def test_consumer_key_changes_when_password_changes() -> None:
    route_a = _route()
    route_a.sasl_password = "old-password"
    route_b = _route()
    route_b.sasl_password = "new-password"

    assert _consumer_key(route_a) != _consumer_key(route_b)


@pytest.mark.unit
def test_consumer_key_changes_when_topic_patterns_change() -> None:
    route_a = _route(include=r"^app\.", exclude="^__", error=r"\.errors?$")
    route_b = _route(include=r"^other\.", exclude="^__", error=r"\.errors?$")

    assert _consumer_key(route_a) != _consumer_key(route_b)


@pytest.mark.unit
def test_consumer_config_includes_route_sasl() -> None:
    route = _route()
    route.bootstrap_servers = "cloud:9092"
    route.security_protocol = "SASL_SSL"
    route.sasl_mechanism = "SCRAM-SHA-256"
    route.sasl_username = "user"
    route.sasl_password = "pass"

    config = _consumer_config(route)

    assert config["bootstrap.servers"] == "cloud:9092"
    assert config["group.id"] == f"ai-events-{TENANT_ID}"
    assert config["security.protocol"] == "SASL_SSL"
    assert config["sasl.mechanism"] == "SCRAM-SHA-256"
    assert config["sasl.username"] == "user"
    assert config["sasl.password"] == "pass"


@pytest.mark.unit
def test_subscription_pattern_uses_include_pattern_when_configured() -> None:
    route = _route(include=r"^myapp\.")
    assert _subscription_pattern(route) == r"^myapp\."


@pytest.mark.unit
def test_subscription_pattern_defaults_to_non_internal_topics() -> None:
    route = _route()
    assert _subscription_pattern(route) == r"^[^_].*"


@pytest.mark.unit
def test_compile_route_treats_none_string_include_pattern_as_blank() -> None:
    row = MagicMock()
    row.tenant_id = TENANT_ID
    row.bootstrap_servers = "broker:9092"
    row.sasl_password_encrypted = None
    row.security_protocol = None
    row.sasl_mechanism = None
    row.sasl_username = None
    row.topic_include_pattern = "None"
    row.topic_exclude_pattern = "^__"
    row.error_topic_pattern = r"\.errors?$"
    row.event_name_fields = ["event_name"]

    route = _compile_route(row)

    assert route is not None
    assert route.include_re is None


# ---------------------------------------------------------------------------
# _extract_event_name
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_extract_event_name_first_matching_field() -> None:
    msg = {"type": "checkout", "event_name": "other"}
    assert _extract_event_name(msg, ["type", "event_name"], "my-topic") == "checkout"


@pytest.mark.unit
def test_extract_event_name_falls_back_through_fields() -> None:
    msg = {"action": "signup"}
    assert _extract_event_name(msg, ["event_name", "type", "action"], "my-topic") == "signup"


@pytest.mark.unit
def test_extract_event_name_falls_back_to_topic() -> None:
    assert _extract_event_name({}, ["event_name", "type"], "my-topic") == "my-topic"


# ---------------------------------------------------------------------------
# _matches_tenant
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_matches_tenant_no_patterns() -> None:
    route = _route(exclude=None)
    assert _matches_tenant(route, "any-topic") is True


@pytest.mark.unit
def test_matches_tenant_exclude_blocks() -> None:
    route = _route(exclude="^__")
    assert _matches_tenant(route, "__consumer_offsets") is False
    assert _matches_tenant(route, "my-events") is True


@pytest.mark.unit
def test_matches_tenant_include_filters() -> None:
    route = _route(include=r"^myapp\.")
    assert _matches_tenant(route, "myapp.events") is True
    assert _matches_tenant(route, "other.events") is False


# ---------------------------------------------------------------------------
# _ingest_event_message
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ingest_event_uses_first_matching_field() -> None:
    route = _route(event_name_fields=["type", "event_name"])
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(MagicMock(), {"type": "checkout", "user_id": "u1"}, route, "t", NOW)
        assert mock_ingest.call_args.kwargs["event_name"] == "checkout"


@pytest.mark.unit
def test_ingest_event_falls_back_to_topic_name() -> None:
    route = _route(event_name_fields=["event_name"])
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(MagicMock(), {"amount": 9.99}, route, "checkout-topic", NOW)
        assert mock_ingest.call_args.kwargs["event_name"] == "checkout-topic"


@pytest.mark.unit
def test_ingest_event_iso_timestamp() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(
            MagicMock(),
            {"event_name": "page_view", "timestamp": "2024-06-01T10:00:00"},
            route, "t", NOW,
        )
        assert mock_ingest.call_args.kwargs["timestamp"] == datetime.datetime(2024, 6, 1, 10, 0, 0)


@pytest.mark.unit
def test_ingest_event_unix_timestamp() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(
            MagicMock(),
            {"event_name": "page_view", "timestamp": 1704067200},
            route, "t", NOW,
        )
        assert mock_ingest.call_args.kwargs["timestamp"] == datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)


@pytest.mark.unit
def test_ingest_event_invalid_timestamp_falls_back_to_now() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(
            MagicMock(),
            {"event_name": "page_view", "timestamp": "not-a-date"},
            route, "t", NOW,
        )
        assert mock_ingest.call_args.kwargs["timestamp"] == NOW


@pytest.mark.unit
def test_ingest_event_strips_reserved_keys_from_properties() -> None:
    route = _route()
    msg = {
        "event_name": "checkout", "user_id": "u1", "tenant_id": "t1",
        "timestamp": "2024-01-01T00:00:00", "amount": 99.99,
    }
    with patch("app.workers.kafka_consumer.ingest_event") as mock_ingest:
        _ingest_event_message(MagicMock(), msg, route, "t", NOW)
        props = mock_ingest.call_args.kwargs["properties"]
        assert "amount" in props
        assert "event_name" not in props
        assert "user_id" not in props


# ---------------------------------------------------------------------------
# _ingest_error_message
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ingest_error_standard_fields() -> None:
    route = _route()
    msg = {"error_type": "TimeoutError", "message": "timed out", "service": "api"}
    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), msg, route, NOW)
        kwargs = mock_upsert.call_args.kwargs
        assert kwargs["error_type"] == "TimeoutError"
        assert kwargs["message"] == "timed out"
        assert kwargs["service"] == "api"


@pytest.mark.unit
def test_ingest_error_type_alias() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), {"type": "ValueError", "msg": "bad value"}, route, NOW)
        kwargs = mock_upsert.call_args.kwargs
        assert kwargs["error_type"] == "ValueError"
        assert kwargs["message"] == "bad value"


@pytest.mark.unit
def test_ingest_error_stack_trace_aliases() -> None:
    route = _route()
    for key in ("stack_trace", "stacktrace", "stack", "traceback"):
        with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
            _ingest_error_message(
                MagicMock(),
                {"error_type": "E", "message": "m", key: "line 42"},
                route, NOW,
            )
            assert mock_upsert.call_args.kwargs["stack_trace"] == "line 42", f"failed for {key}"


@pytest.mark.unit
def test_ingest_error_drops_when_missing_required_fields() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), {"error_type": "E"}, route, NOW)
        mock_upsert.assert_not_called()

    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), {"message": "m"}, route, NOW)
        mock_upsert.assert_not_called()


@pytest.mark.unit
def test_ingest_error_extra_fields_become_metadata() -> None:
    route = _route()
    msg = {"error_type": "E", "message": "m", "request_id": "abc", "env": "prod"}
    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), msg, route, NOW)
        assert mock_upsert.call_args.kwargs["error_metadata"] == {"request_id": "abc", "env": "prod"}


@pytest.mark.unit
def test_ingest_error_default_severity() -> None:
    route = _route()
    with patch("app.workers.kafka_consumer.upsert_error") as mock_upsert:
        _ingest_error_message(MagicMock(), {"error_type": "E", "message": "m"}, route, NOW)
        assert mock_upsert.call_args.kwargs["severity"] == "error"
