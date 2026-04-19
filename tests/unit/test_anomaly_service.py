"""
Unit tests for anomaly detection logic that don't require a database.
"""
import pytest

from app.services.anomaly import _severity


@pytest.mark.unit
def test_severity_low() -> None:
    assert _severity(3.0) == "medium"
    assert _severity(-3.0) == "medium"


@pytest.mark.unit
def test_severity_buckets() -> None:
    assert _severity(2.9) == "low"
    assert _severity(3.0) == "medium"
    assert _severity(4.0) == "high"
    assert _severity(5.0) == "critical"


@pytest.mark.unit
def test_severity_negative_deviations() -> None:
    assert _severity(-4.5) == "high"
    assert _severity(-5.1) == "critical"
