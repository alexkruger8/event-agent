"""
Integration tests for conversational knowledge learning.
Tests DB persistence of event type descriptions and metadata.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.llm.conversation import (
    _explore_event_properties,
    _knowledge_gap_prompt,
    _load_event_type_knowledge,
    _update_event_type_knowledge,
)
from app.models.event import Events, EventTypes
from app.models.tenant import Tenants


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_event_type(db: Session, tenant_id: uuid.UUID, event_name: str) -> EventTypes:
    now = datetime.datetime.now(datetime.UTC)
    et = EventTypes(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        first_seen=now,
        last_seen=now,
        total_events=100,
    )
    db.add(et)
    db.flush()
    return et


@pytest.mark.integration
def test_update_event_type_knowledge_persists(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(db, tenant_id, "checkout")

    result = _update_event_type_knowledge(
        event_name="checkout",
        description="Represents a completed purchase",
        metadata={"category": "commerce"},
        db=db,
        tenant_id=tenant_id,
    )

    assert "checkout" in result
    assert "Represents a completed purchase" in result

    et = db.query(EventTypes).filter(
        EventTypes.tenant_id == tenant_id,
        EventTypes.event_name == "checkout",
    ).one()
    assert et.description == "Represents a completed purchase"
    assert et.type_metadata == {"category": "commerce"}


@pytest.mark.integration
def test_update_event_type_knowledge_merges_metadata(db: Session, tenant_id: uuid.UUID) -> None:
    et = _make_event_type(db, tenant_id, "page_view")
    et.type_metadata = {"category": "navigation"}
    db.flush()

    _update_event_type_knowledge(
        event_name="page_view",
        description=None,
        metadata={"related_events": ["page_exit", "scroll"]},
        db=db,
        tenant_id=tenant_id,
    )

    db.refresh(et)
    assert et.type_metadata == {"category": "navigation", "related_events": ["page_exit", "scroll"]}


@pytest.mark.integration
def test_update_rejects_unknown_event_name(db: Session, tenant_id: uuid.UUID) -> None:
    result = _update_event_type_knowledge(
        event_name="nonexistent_event",
        description="Should not be saved",
        metadata=None,
        db=db,
        tenant_id=tenant_id,
    )

    assert "Error" in result
    assert "nonexistent_event" in result


@pytest.mark.integration
def test_load_event_type_knowledge_formats_correctly(db: Session, tenant_id: uuid.UUID) -> None:
    et1 = _make_event_type(db, tenant_id, "checkout")
    et1.description = "Represents a completed purchase"
    et1.type_metadata = {"category": "commerce", "related_events": ["add_to_cart"]}

    _make_event_type(db, tenant_id, "signup")
    # no description or metadata

    db.flush()

    snapshot = _load_event_type_knowledge(db, tenant_id)

    assert "checkout" in snapshot
    assert '"Represents a completed purchase"' in snapshot
    assert "category: commerce" in snapshot
    assert "related: add_to_cart" in snapshot
    assert "signup" in snapshot
    assert "(no description yet)" in snapshot


@pytest.mark.integration
def test_knowledge_gap_prompt_returns_directive_when_no_description(
    db: Session, tenant_id: uuid.UUID
) -> None:
    _make_event_type(db, tenant_id, "signup")  # no description

    result = _knowledge_gap_prompt("signup", db, tenant_id)

    assert result != ""
    assert "signup" in result


@pytest.mark.integration
def test_knowledge_gap_prompt_empty_when_described(db: Session, tenant_id: uuid.UUID) -> None:
    et = _make_event_type(db, tenant_id, "signup")
    et.description = "New user registration"
    db.flush()

    result = _knowledge_gap_prompt("signup", db, tenant_id)

    assert result == ""


@pytest.mark.integration
def test_knowledge_gap_prompt_returns_directive_when_event_type_missing(
    db: Session, tenant_id: uuid.UUID
) -> None:
    # No EventTypes row at all
    result = _knowledge_gap_prompt("unknown_event", db, tenant_id)

    assert result != ""
    assert "unknown_event" in result


# ── Property discovery ────────────────────────────────────────────────────────

def _insert_event(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    properties: dict,  # type: ignore[type-arg]
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    db.add(Events(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        timestamp=now,
        ingested_at=now,
        properties=properties,
    ))
    db.flush()


@pytest.mark.integration
def test_explore_returns_not_found_for_missing_event(db: Session, tenant_id: uuid.UUID) -> None:
    result = _explore_event_properties("nonexistent", 200, db, tenant_id)
    assert "No events found" in result


@pytest.mark.integration
def test_explore_identifies_numeric_properties(db: Session, tenant_id: uuid.UUID) -> None:
    for i in range(10):
        _insert_event(db, tenant_id, "checkout", {"amount": str(float(i + 1) * 10), "items": str(i + 1)})

    result = _explore_event_properties("checkout", 200, db, tenant_id)

    assert "amount" in result
    assert "items" in result
    assert "★ numeric" in result
    assert "Suggested properties to track" in result
    assert "amount" in result.split("Suggested properties")[1]


@pytest.mark.integration
def test_explore_flags_non_numeric_properties(db: Session, tenant_id: uuid.UUID) -> None:
    for _ in range(10):
        _insert_event(db, tenant_id, "signup", {"plan": "pro", "referral_code": "ABC123"})

    result = _explore_event_properties("signup", 200, db, tenant_id)

    assert "plan" in result
    assert "No consistently numeric properties found" in result


@pytest.mark.integration
def test_explore_reports_presence_percentage(db: Session, tenant_id: uuid.UUID) -> None:
    # 5 events with amount, 5 without
    for i in range(5):
        _insert_event(db, tenant_id, "checkout", {"amount": str(float(i) * 10)})
    for _ in range(5):
        _insert_event(db, tenant_id, "checkout", {})

    result = _explore_event_properties("checkout", 200, db, tenant_id)

    assert "50%" in result


@pytest.mark.integration
def test_explore_tenant_isolation(db: Session, tenant_id: uuid.UUID) -> None:
    other_id = uuid.uuid4()
    db.add(Tenants(id=other_id, name="other", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()

    # Insert events only for the other tenant
    for i in range(5):
        _insert_event(db, other_id, "checkout", {"amount": str(float(i) * 10)})

    result = _explore_event_properties("checkout", 200, db, tenant_id)
    assert "No events found" in result
