"""
Integration tests for the insight generation service.
Mocks the LLM call — tests DB persistence only.
Requires a running database (docker compose up -d).
"""
import datetime
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.llm.insights import InsightOutput
from app.models.anomaly import Anomalies
from app.models.event import EventTypes
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.services.insight import _format_event_knowledge, generate_insights


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_anomaly(db: Session, tenant_id: uuid.UUID) -> Anomalies:
    now = datetime.datetime.now(datetime.UTC)
    metric = Metrics(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_name="event_count.page_view",
        metric_timestamp=now, value=480.0, tags={}, created_at=now,
    )
    db.add(metric)
    db.flush()

    anomaly = Anomalies(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_id=metric.id,
        metric_name="event_count.page_view", metric_timestamp=now,
        current_value=480.0, baseline_value=120.0, deviation_percent=300.0,
        severity="critical", detected_at=now, context={},
    )
    db.add(anomaly)
    db.flush()
    return anomaly


_FAKE_OUTPUT = InsightOutput(
    title="Page views spiked 4x above normal",
    summary="Page view count hit 480, far above the baseline of 120.",
    explanation="This spike likely indicates a traffic surge. Investigate referral sources.",
    confidence=0.85,
)


@pytest.mark.integration
def test_generates_and_persists_insight(db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)

    with patch("app.services.insight.generate_insight", return_value=_FAKE_OUTPUT):
        with patch("app.services.insight.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-opus-4-6"
            insights = generate_insights(db, [anomaly])

    assert len(insights) == 1
    insight = insights[0]
    assert insight.title == _FAKE_OUTPUT.title
    assert insight.anomaly_id == anomaly.id
    assert insight.tenant_id == tenant_id
    assert insight.confidence == pytest.approx(0.85)


@pytest.mark.integration
def test_skips_when_no_api_key(db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)

    with patch("app.services.insight.settings") as mock_settings:
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.llm_configured = False
        insights = generate_insights(db, [anomaly])

    assert insights == []


@pytest.mark.integration
def test_empty_anomalies_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    insights = generate_insights(db, [])
    assert insights == []


@pytest.mark.integration
def test_continues_after_llm_failure(db: Session, tenant_id: uuid.UUID) -> None:
    anomaly1 = _make_anomaly(db, tenant_id)
    anomaly2 = _make_anomaly(db, tenant_id)

    call_count = 0
    def flaky_generate(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("API timeout")
        return _FAKE_OUTPUT

    with patch("app.services.insight.generate_insight", side_effect=flaky_generate):
        with patch("app.services.insight.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-opus-4-6"
            insights = generate_insights(db, [anomaly1, anomaly2])

    # First call failed, second succeeded
    assert len(insights) == 1


@pytest.mark.integration
def test_event_knowledge_passed_to_llm(db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)

    et = EventTypes(
        id=uuid.uuid4(), tenant_id=tenant_id, event_name="page_view",
        description="A user viewed a page in the web app",
        type_metadata={"category": "navigation", "business_context": "Core engagement metric"},
    )
    db.add(et)
    db.flush()

    with patch("app.services.insight.generate_insight", return_value=_FAKE_OUTPUT) as mock_gen:
        with patch("app.services.insight.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-opus-4-6"
            generate_insights(db, [anomaly])

    _, kwargs = mock_gen.call_args
    assert kwargs["event_knowledge"] is not None
    assert "A user viewed a page in the web app" in kwargs["event_knowledge"]
    assert "navigation" in kwargs["event_knowledge"]
    assert "Core engagement metric" in kwargs["event_knowledge"]


@pytest.mark.integration
def test_no_event_knowledge_when_undescribed(db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)
    # No EventTypes row for this tenant — knowledge should be None

    with patch("app.services.insight.generate_insight", return_value=_FAKE_OUTPUT) as mock_gen:
        with patch("app.services.insight.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-opus-4-6"
            generate_insights(db, [anomaly])

    _, kwargs = mock_gen.call_args
    assert kwargs["event_knowledge"] is None


def test_format_event_knowledge_all_fields() -> None:
    et = EventTypes(
        id=uuid.uuid4(),
        event_name="checkout",
        description="Completed purchase",
        type_metadata={
            "category": "commerce",
            "business_context": "Primary revenue event",
            "related_events": ["add_to_cart", "payment_failed"],
        },
    )
    result = _format_event_knowledge(et)
    assert result is not None
    assert 'Description: "Completed purchase"' in result
    assert "Category: commerce" in result
    assert "Business context: Primary revenue event" in result
    assert "add_to_cart, payment_failed" in result


def test_format_event_knowledge_empty_returns_none() -> None:
    et = EventTypes(id=uuid.uuid4(), event_name="checkout")
    assert _format_event_knowledge(et) is None
