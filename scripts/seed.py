"""
Seed script: creates tenants and demo data on first start.

Idempotent — skips all inserts if any tenant already exists.

Tenants created:
  - "My Tenant"    — always created; empty, ready for the user's real environment
  - "Demo Tenant"  — created when INCLUDE_DEMO_TENANT=true (default); pre-populated
                     with 8 days of metrics, event types, and baked-in anomalies so
                     you can explore the platform before connecting real data

Usage:
    INCLUDE_DEMO_TENANT=true python scripts/seed.py
"""
import datetime
import json
import logging
import os
import random
import sys
import uuid

sys.path.insert(0, ".")

from sqlalchemy.orm import Session

from app.config import settings
from app.database.engine import get_engine
from app.models.anomaly import Anomalies  # noqa: F401 — registers ORM relationships
from app.models.event import Events, EventTypes
from app.models.insight import Insights  # noqa: F401
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.models.tenant_kafka_settings import TenantKafkaSettings  # noqa: F401
from app.models.trend import Trends  # noqa: F401

logger = logging.getLogger(__name__)

SEED = 42
random.seed(SEED)

# Event types: name → (mean, stddev, description, category, business_context)
EVENT_PROFILES: dict[str, tuple[float, float, str, str, str]] = {
    "page_view": (
        120.0, 15.0,
        "A user loaded a page in the web app.",
        "engagement",
        "Core traffic signal; spikes may indicate viral content or bot activity.",
    ),
    "signup": (
        12.0, 2.0,
        "A new user completed registration.",
        "acquisition",
        "Primary growth metric; droughts may indicate funnel breakage or auth issues.",
    ),
    "checkout": (
        6.0, 1.5,
        "A user completed a purchase checkout.",
        "commerce",
        "Revenue-generating event; drops directly impact revenue.",
    ),
    "button_click": (
        80.0, 10.0,
        "A user clicked an interactive button in the UI.",
        "engagement",
        "General interaction signal; useful for detecting UI regressions.",
    ),
}

LOOKBACK_DAYS = 35  # 5 full weeks — gives 5 samples per (weekday, hour) slot
HOURS_PER_DAY = 24


def _normal(mean: float, stddev: float) -> float:
    return max(0.0, random.gauss(mean, stddev))


def _create_demo_tenant(db: Session, now: datetime.datetime) -> None:
    tenant_id = uuid.uuid4()
    db.add(Tenants(id=tenant_id, name="Demo Tenant", created_at=now))
    db.flush()
    print(f"Created tenant: {tenant_id}  (Demo Tenant)")

    # Event types with descriptions
    # checkout tracks amount and items_count so property metrics work out of the box
    extra_metadata: dict[str, dict] = {  # type: ignore[type-arg]
        "checkout": {"tracked_properties": {"amount": ["avg", "p95"], "items_count": ["avg"]}},
    }
    for event_name, (_, _, description, category, business_context) in EVENT_PROFILES.items():
        metadata: dict = {  # type: ignore[type-arg]
            "category": category,
            "business_context": business_context,
            **extra_metadata.get(event_name, {}),
        }
        db.add(EventTypes(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_name=event_name,
            first_seen=now - datetime.timedelta(days=LOOKBACK_DAYS),
            last_seen=now,
            total_events=0,
            description=description,
            type_metadata=metadata,
        ))
    db.flush()
    print(f"Created {len(EVENT_PROFILES)} event types with descriptions")

    # Historical metrics (8 days × 24 hours)
    metric_count = 0
    for day in range(LOOKBACK_DAYS, 0, -1):
        for hour in range(HOURS_PER_DAY):
            ts = now - datetime.timedelta(days=day, hours=hour)
            for event_name, (mean, stddev, *_) in EVENT_PROFILES.items():
                value = round(_normal(mean, stddev))
                db.add(Metrics(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    metric_name=f"event_count.{event_name}",
                    metric_timestamp=ts,
                    value=value,
                    tags={"event_name": event_name},
                    created_at=ts,
                ))
                metric_count += 1
    db.flush()
    print(f"Inserted {metric_count} historical metric rows")

    # Recent raw events with baked-in anomalies
    event_count = 0

    def insert_events(
        event_name: str,
        count: int,
        window_minutes: int = 55,
        properties_fn: None = None,
    ) -> None:
        nonlocal event_count
        for _ in range(count):
            ts = now - datetime.timedelta(minutes=random.uniform(0, window_minutes))
            props: dict[str, object] = properties_fn() if properties_fn is not None else {}
            db.add(Events(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                event_name=event_name,
                timestamp=ts,
                ingested_at=now,
                properties=props,
            ))
            event_count += 1

    def _checkout_props() -> dict:  # type: ignore[type-arg]
        return {
            "amount": round(_normal(49.99, 15.0), 2),
            "items_count": max(1, int(_normal(3.0, 1.0))),
        }

    insert_events("checkout", int(_normal(6, 1.5)), properties_fn=_checkout_props)  # type: ignore[arg-type]
    insert_events("button_click", int(_normal(80, 10)))
    insert_events("page_view", 480)
    print("Baked in anomaly: page_view SPIKE  (480 events vs ~120 baseline)")
    insert_events("signup", 1)
    print("Baked in anomaly: signup   DROUGHT (1 event vs ~12 baseline)")

    db.flush()
    print(f"Inserted {event_count} raw events in the current window")

    publish_kafka_demo = os.getenv("PUBLISH_DEMO_KAFKA_EVENTS", "false").lower() in ("true", "1", "yes")
    if publish_kafka_demo and settings.kafka_bootstrap_servers:
        _publish_demo_events_to_kafka(tenant_id, event_count)
    elif publish_kafka_demo:
        print("PUBLISH_DEMO_KAFKA_EVENTS=true but KAFKA_BOOTSTRAP_SERVERS is blank — skipping Kafka seed publish")


def _publish_demo_events_to_kafka(tenant_id: uuid.UUID, event_count: int) -> None:
    """Publish a sample of demo events to the configured Kafka/Redpanda broker."""
    try:
        from confluent_kafka import Producer
        from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]
    except ImportError:
        print("confluent-kafka not installed — skipping Kafka seed publish")
        return

    topic = "demo.events"
    error_topic = "demo.errors"

    # Ensure topics exist
    bootstrap = str(settings.kafka_bootstrap_servers)
    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = admin.list_topics(timeout=5).topics
    to_create = [
        NewTopic(t, num_partitions=1, replication_factor=1)
        for t in (topic, error_topic)
        if t not in existing
    ]
    if to_create:
        admin.create_topics(to_create)

    producer = Producer({"bootstrap.servers": bootstrap})

    sample_events: list[dict[str, object]] = [
        {"event_name": "page_view", "user_id": "u1", "properties": {}},
        {"event_name": "checkout", "user_id": "u2", "amount": round(random.uniform(20, 200), 2)},
        {"event_name": "signup", "user_id": "u3"},
    ]
    for evt in sample_events * 3:
        payload = {**evt, "tenant_id": str(tenant_id)}
        producer.produce(topic, json.dumps(payload).encode())

    # Publish a sample error
    producer.produce(error_topic, json.dumps({
        "error_type": "TimeoutError",
        "message": "Payment gateway timed out after 30s",
        "service": "payment-api",
        "severity": "error",
        "tenant_id": str(tenant_id),
    }).encode())

    producer.flush()
    print(f"Published demo events and 1 error to Kafka topics '{topic}' / '{error_topic}'")


def seed() -> None:
    include_demo = os.getenv("INCLUDE_DEMO_TENANT", "true").lower() not in ("false", "0", "no")
    engine = get_engine()
    now = datetime.datetime.now(datetime.UTC)

    with Session(engine) as db:
        # Idempotency check
        if db.query(Tenants).count() > 0:
            print("Tenants already exist — skipping seed.")
            return

        # My Tenant — always created, empty
        my_tenant_id = uuid.uuid4()
        db.add(Tenants(id=my_tenant_id, name="My Tenant", created_at=now))
        db.flush()
        print(f"Created tenant: {my_tenant_id}  (My Tenant)")

        # Demo Tenant — pre-populated, optional
        if include_demo:
            _create_demo_tenant(db, now)
        else:
            print("INCLUDE_DEMO_TENANT=false — skipping demo data")

        db.commit()
        print("\nDone. The worker will trigger the pipeline automatically.")
        print("Or trigger manually: curl -X POST http://localhost:8000/admin/run-pipeline -H 'X-API-Key: <your-key>'")


if __name__ == "__main__":
    seed()
