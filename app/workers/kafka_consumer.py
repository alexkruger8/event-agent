"""
Kafka / Redpanda consumer worker.

Auto-subscribes to all topics on the configured cluster and routes each message
to the correct tenant based on per-tenant include/exclude patterns.

Topic routing logic per message:
  1. Skip if topic matches the tenant's exclude_pattern (default: ^__)
  2. Skip if tenant has an include_pattern and the topic does not match it
  3. Treat as error stream if topic matches error_topic_pattern (default: \.errors?$)
  4. Otherwise treat as event stream — try event_name_fields in order, fall back to topic name

If no TenantKafkaSettings rows exist and there is exactly one tenant in the DB,
that tenant receives all messages with default settings (no filtering).
"""

import datetime
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database.session import _get_session_local
from app.models.tenant import Tenants
from app.models.tenant_kafka_settings import TenantKafkaSettings
from app.security.encryption import EncryptionConfigurationError, decrypt_secret
from app.services.error_ingestion import compute_fingerprint, upsert_error
from app.services.event_ingestion import ingest_event

logger = logging.getLogger(__name__)

_EVENT_RESERVED = {"event_name", "user_id", "timestamp", "tenant_id"}
_ERROR_TYPE_ALIASES = ("error_type", "type", "exception")
_MESSAGE_ALIASES = ("message", "msg", "error_message")
_STACK_ALIASES = ("stack_trace", "stacktrace", "stack", "traceback")


@dataclass
class _TenantRoute:
    tenant_id: uuid.UUID
    bootstrap_servers: str
    include_re: re.Pattern[str] | None
    exclude_re: re.Pattern[str] | None
    error_re: re.Pattern[str]
    security_protocol: str | None = None
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    event_name_fields: list[str] = field(default_factory=lambda: ["event_name", "type", "action", "name"])


def _compile_route(row: TenantKafkaSettings) -> _TenantRoute | None:
    bootstrap_servers = (row.bootstrap_servers or settings.kafka_bootstrap_servers or "").strip()
    if not bootstrap_servers:
        logger.warning("Skipping tenant %s — no Kafka broker configured", row.tenant_id)
        return None
    sasl_password: str | None = None
    if row.sasl_password_encrypted:
        try:
            sasl_password = decrypt_secret(row.sasl_password_encrypted)
        except EncryptionConfigurationError as exc:
            row.last_connect_error = str(exc)
            logger.error("Skipping tenant %s — %s", row.tenant_id, exc)
            return None
    return _TenantRoute(
        tenant_id=uuid.UUID(str(row.tenant_id)),
        bootstrap_servers=bootstrap_servers,
        security_protocol=row.security_protocol,
        sasl_mechanism=row.sasl_mechanism,
        sasl_username=row.sasl_username,
        sasl_password=sasl_password,
        include_re=re.compile(row.topic_include_pattern) if row.topic_include_pattern else None,
        exclude_re=re.compile(row.topic_exclude_pattern) if row.topic_exclude_pattern else None,
        error_re=re.compile(row.error_topic_pattern),
        event_name_fields=list(row.event_name_fields or ["event_name", "type", "action", "name"]),
    )


def _default_route(tenant_id: uuid.UUID) -> _TenantRoute:
    return _TenantRoute(
        tenant_id=tenant_id,
        bootstrap_servers=settings.kafka_bootstrap_servers or "",
        security_protocol=None,
        sasl_mechanism=None,
        sasl_username=None,
        sasl_password=None,
        include_re=None,
        exclude_re=re.compile("^__"),
        error_re=re.compile(r"\.errors?$"),
    )


def _load_routes(db: Session) -> list[_TenantRoute]:
    try:
        rows = (
            db.query(TenantKafkaSettings)
            .filter(TenantKafkaSettings.enabled.is_(True))
            .all()
        )
        if rows:
            return [route for row in rows if (route := _compile_route(row)) is not None]

        if not settings.kafka_bootstrap_servers:
            logger.info("No Kafka settings configured and no global broker is set")
            return []

        # Auto-detect: if exactly one tenant exists, use it with default settings
        tenants = db.query(Tenants).limit(2).all()
        if len(tenants) == 1:
            logger.info(
                "No Kafka settings configured — auto-routing to sole tenant %s", tenants[0].id
            )
            return [_default_route(uuid.UUID(str(tenants[0].id)))]

        logger.warning(
            "No Kafka settings configured and multiple tenants exist — "
            "configure Kafka settings per tenant in the Settings tab"
        )
        return []
    finally:
        db.commit()


def _group_id(route: _TenantRoute) -> str:
    prefix = settings.kafka_consumer_group_prefix or "ai-events"
    return f"{prefix}-{route.tenant_id}"


def _consumer_config(route: _TenantRoute) -> dict[str, Any]:
    config: dict[str, Any] = {
        "bootstrap.servers": route.bootstrap_servers,
        "group.id": _group_id(route),
        "auto.offset.reset": settings.kafka_auto_offset_reset,
        "session.timeout.ms": settings.kafka_session_timeout_ms,
        "enable.auto.commit": True,
    }
    if route.security_protocol:
        config["security.protocol"] = route.security_protocol
    if route.sasl_mechanism:
        config["sasl.mechanism"] = route.sasl_mechanism
    if route.sasl_username:
        config["sasl.username"] = route.sasl_username
    if route.sasl_password:
        config["sasl.password"] = route.sasl_password
    return config


def _consumer_key(route: _TenantRoute) -> str:
    parts = [
        str(route.tenant_id),
        route.bootstrap_servers,
        route.security_protocol or "",
        route.sasl_mechanism or "",
        route.sasl_username or "",
        hashlib.sha256(route.sasl_password.encode("utf-8")).hexdigest()
        if route.sasl_password
        else "",
    ]
    return "|".join(parts)


def _sync_consumers(
    consumers: dict[str, tuple[Any, _TenantRoute]],
    routes: list[_TenantRoute],
    consumer_cls: Any,
    db: Session,
) -> None:
    routes_by_key = {_consumer_key(route): route for route in routes}
    wanted = set(routes_by_key)

    for key in list(consumers):
        if key not in wanted:
            consumer, route = consumers[key]
            consumer.close()
            del consumers[key]
            logger.info("Closed Kafka consumer for tenant %s", route.tenant_id)

    for key in sorted(wanted):
        if key in consumers:
            continue
        route = routes_by_key[key]
        consumer = consumer_cls(_consumer_config(route))
        consumer.subscribe([r"^[^_].*"])  # all topics not starting with _
        consumers[key] = (consumer, route)
        row = (
            db.query(TenantKafkaSettings)
            .filter(TenantKafkaSettings.tenant_id == route.tenant_id)
            .first()
        )
        if row is not None:
            row.last_connect_at = datetime.datetime.now(datetime.UTC)
            row.last_connect_error = None
            db.commit()
        logger.info(
            "Subscribed to all topics on brokers %s for tenant %s",
            route.bootstrap_servers,
            route.tenant_id,
        )


def _matches_tenant(route: _TenantRoute, topic: str) -> bool:
    if route.exclude_re and route.exclude_re.search(topic):
        return False
    if route.include_re and not route.include_re.search(topic):
        return False
    return True


def _extract_event_name(msg: dict[str, Any], fields: list[str], topic: str) -> str:
    for f in fields:
        val = msg.get(f)
        if val and isinstance(val, str):
            return str(val)
    return topic


def _ingest_event_message(
    db: Session,
    msg: dict[str, Any],
    route: _TenantRoute,
    topic: str,
    now: datetime.datetime,
) -> None:
    event_name = _extract_event_name(msg, route.event_name_fields, topic)
    raw_ts = msg.get("timestamp")
    if isinstance(raw_ts, str):
        try:
            ts: datetime.datetime = datetime.datetime.fromisoformat(raw_ts)
        except ValueError:
            ts = now
    elif isinstance(raw_ts, (int, float)):
        ts = datetime.datetime.fromtimestamp(raw_ts, tz=datetime.UTC)
    else:
        ts = now

    properties = {k: v for k, v in msg.items() if k not in _EVENT_RESERVED}
    ingest_event(
        db,
        tenant_id=route.tenant_id,
        event_name=event_name,
        user_id=str(msg["user_id"]) if "user_id" in msg else None,
        timestamp=ts,
        properties=properties,
        ingested_at=now,
    )


def _ingest_error_message(
    db: Session,
    msg: dict[str, Any],
    route: _TenantRoute,
    now: datetime.datetime,
) -> None:
    error_type = next((msg[k] for k in _ERROR_TYPE_ALIASES if k in msg), None)
    message = next((msg[k] for k in _MESSAGE_ALIASES if k in msg), None)
    if not error_type or not message:
        logger.warning("Dropping error message — missing error_type or message: %s", msg)
        return

    stack_trace = next((msg[k] for k in _STACK_ALIASES if k in msg), None)
    service = msg.get("service")
    component = msg.get("component")
    severity = msg.get("severity", "error")
    fingerprint = msg.get("fingerprint") or compute_fingerprint(
        str(error_type), str(message), str(service) if service else None
    )
    reserved = {
        *_ERROR_TYPE_ALIASES, *_MESSAGE_ALIASES, *_STACK_ALIASES,
        "service", "component", "severity", "fingerprint", "timestamp", "tenant_id",
    }
    metadata: dict[str, Any] | None = {k: v for k, v in msg.items() if k not in reserved} or None
    upsert_error(
        db,
        tenant_id=route.tenant_id,
        error_type=str(error_type),
        message=str(message),
        stack_trace=str(stack_trace) if stack_trace else None,
        service=str(service) if service else None,
        component=str(component) if component else None,
        severity=str(severity),
        fingerprint=str(fingerprint),
        error_metadata=metadata,
        now=now,
    )


def _record_message(db: Session, route: _TenantRoute, topic: str, now: datetime.datetime) -> None:
    row = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == route.tenant_id)
        .first()
    )
    if row is None:
        return
    row.last_message_at = now
    row.last_message_topic = topic
    row.last_connect_error = None
    row.messages_ingested_count = (row.messages_ingested_count or 0) + 1


def _record_connect_error(db: Session, route: _TenantRoute, error: str) -> None:
    row = (
        db.query(TenantKafkaSettings)
        .filter(TenantKafkaSettings.tenant_id == route.tenant_id)
        .first()
    )
    if row is None:
        return
    row.last_connect_error = error
    db.commit()


def run_consumer(stop_event: threading.Event) -> None:
    """
    Main consumer loop. Blocks until stop_event is set.
    Requires confluent_kafka and at least one global or tenant-level broker.
    """
    try:
        from confluent_kafka import Consumer, KafkaError, KafkaException
    except ImportError:
        logger.error("confluent-kafka is not installed. Run: pip install confluent-kafka")
        return

    SessionLocal = _get_session_local()
    db = SessionLocal()
    consumers: dict[str, tuple[Any, _TenantRoute]] = {}

    try:
        routes = _load_routes(db)
        if routes:
            _sync_consumers(consumers, routes, Consumer, db)
        else:
            logger.info("No routes configured — waiting for tenant Kafka settings to be added")

        last_refresh = time.monotonic()

        while not stop_event.is_set():
            if time.monotonic() - last_refresh >= settings.kafka_topic_refresh_interval_seconds:
                new_routes = _load_routes(db)
                if not routes and new_routes:
                    logger.info("Routes became available")
                routes = new_routes
                _sync_consumers(consumers, routes, Consumer, db)
                last_refresh = time.monotonic()

            if not routes:
                time.sleep(1)
                continue

            for key, (consumer, route) in list(consumers.items()):
                try:
                    msg = consumer.poll(timeout=1.0)
                except Exception as exc:
                    logger.exception("Kafka poll failed for tenant %s", route.tenant_id)
                    _record_connect_error(db, route, str(exc))
                    consumer.close()
                    del consumers[key]
                    continue
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    error = KafkaException(msg.error())
                    logger.error("Kafka error for tenant %s: %s", route.tenant_id, error)
                    _record_connect_error(db, route, str(error))
                    continue

                topic = msg.topic()
                try:
                    msg_dict: dict[str, Any] = json.loads(msg.value())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("Failed to parse message on topic '%s': %s", topic, exc)
                    continue

                now = datetime.datetime.now(datetime.UTC)
                if not _matches_tenant(route, topic):
                    continue
                try:
                    if route.error_re.search(topic):
                        _ingest_error_message(db, msg_dict, route, now)
                    else:
                        _ingest_event_message(db, msg_dict, route, topic, now)
                    _record_message(db, route, topic, now)
                    db.commit()
                except Exception:
                    logger.exception(
                        "Failed to ingest message from topic '%s' for tenant %s",
                        topic, route.tenant_id,
                    )
                    db.rollback()
    finally:
        for consumer, _route in consumers.values():
            consumer.close()
        db.close()
        logger.info("Kafka consumer shut down")
