"""
Unit tests for the LLM insight generation module.
Mocks get_llm_client so no API calls are made.
"""
import datetime
import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.llm.insights import InsightOutput, generate_insight
from app.models.anomaly import Anomalies

_FAKE_OUTPUT = InsightOutput(
    title="Page view traffic spiked 4x above normal",
    summary="Page view events jumped to 480, far exceeding the baseline of 120.",
    explanation="This spike may indicate a traffic surge from a marketing campaign or viral content.",
    confidence=0.85,
)


def _make_anomaly() -> Anomalies:
    now = datetime.datetime.now(datetime.UTC)
    a = Anomalies()
    a.id = uuid.uuid4()
    a.tenant_id = uuid.uuid4()
    a.metric_name = "event_count.page_view"
    a.current_value = 480.0
    a.baseline_value = 120.0
    a.deviation_percent = 300.0
    a.severity = "critical"
    a.detected_at = now
    a.context = {"stddev": 15.0, "sample_size": 100, "deviations_from_mean": 24.0}
    return a


def _make_mock_llm_client(output: InsightOutput) -> MagicMock:
    mock = MagicMock()
    mock.complete_json.return_value = json.dumps(output.model_dump())
    return mock


@pytest.mark.unit
def test_generate_insight_returns_structured_output() -> None:
    with patch("app.llm.insights.get_llm_client", return_value=_make_mock_llm_client(_FAKE_OUTPUT)):
        result = generate_insight(_make_anomaly())

    assert result.title == _FAKE_OUTPUT.title
    assert result.confidence == _FAKE_OUTPUT.confidence


@pytest.mark.unit
def test_generate_insight_prompt_includes_metric_name() -> None:
    captured: dict[str, Any] = {}

    mock_client = MagicMock()

    def capturing_complete_json(system: str, prompt: str, **kwargs: Any) -> str:
        captured["system"] = system
        captured["prompt"] = prompt
        return json.dumps(_FAKE_OUTPUT.model_dump())

    mock_client.complete_json.side_effect = capturing_complete_json

    with patch("app.llm.insights.get_llm_client", return_value=mock_client):
        generate_insight(_make_anomaly())

    assert "event_count.page_view" in captured["prompt"]
    assert "480" in captured["prompt"]


@pytest.mark.unit
def test_generate_insight_with_event_knowledge() -> None:
    captured: dict[str, Any] = {}

    mock_client = MagicMock()

    def capturing_complete_json(system: str, prompt: str, **kwargs: Any) -> str:
        captured["prompt"] = prompt
        return json.dumps(_FAKE_OUTPUT.model_dump())

    mock_client.complete_json.side_effect = capturing_complete_json

    with patch("app.llm.insights.get_llm_client", return_value=mock_client):
        generate_insight(_make_anomaly(), event_knowledge="Page view tracks user navigation.")

    assert "Page view tracks user navigation." in captured["prompt"]
