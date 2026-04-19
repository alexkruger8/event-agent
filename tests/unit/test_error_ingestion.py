"""Unit tests for error ingestion service — no database required."""
import datetime
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.error_ingestion import compute_fingerprint, upsert_error


@pytest.mark.unit
def test_fingerprint_is_deterministic() -> None:
    fp1 = compute_fingerprint("ValueError", "something went wrong", "api")
    fp2 = compute_fingerprint("ValueError", "something went wrong", "api")
    assert fp1 == fp2


@pytest.mark.unit
def test_fingerprint_differs_by_type() -> None:
    fp1 = compute_fingerprint("ValueError", "msg", "svc")
    fp2 = compute_fingerprint("TypeError", "msg", "svc")
    assert fp1 != fp2


@pytest.mark.unit
def test_fingerprint_differs_by_message() -> None:
    fp1 = compute_fingerprint("ValueError", "msg-a", "svc")
    fp2 = compute_fingerprint("ValueError", "msg-b", "svc")
    assert fp1 != fp2


@pytest.mark.unit
def test_fingerprint_differs_by_service() -> None:
    fp1 = compute_fingerprint("ValueError", "msg", "svc-a")
    fp2 = compute_fingerprint("ValueError", "msg", "svc-b")
    assert fp1 != fp2


@pytest.mark.unit
def test_fingerprint_none_service_treated_as_empty_string() -> None:
    fp1 = compute_fingerprint("ValueError", "msg", None)
    fp2 = compute_fingerprint("ValueError", "msg", None)
    assert fp1 == fp2
    # Different from having a service
    fp3 = compute_fingerprint("ValueError", "msg", "svc")
    assert fp1 != fp3


@pytest.mark.unit
def test_fingerprint_is_64_char_hex() -> None:
    fp = compute_fingerprint("E", "m", None)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


@pytest.mark.unit
def test_upsert_error_inserts_new_when_no_existing() -> None:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    tenant_id = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC)

    error, was_upserted = upsert_error(
        db,
        tenant_id=tenant_id,
        error_type="TimeoutError",
        message="Request timed out",
        stack_trace=None,
        service="api",
        component=None,
        severity="error",
        fingerprint=None,
        error_metadata=None,
        now=now,
    )

    db.add.assert_called_once()
    assert was_upserted is False
    assert error.occurrence_count == 1
    assert error.tenant_id == tenant_id
    assert error.error_type == "TimeoutError"


@pytest.mark.unit
def test_upsert_error_increments_existing() -> None:
    existing = MagicMock()
    existing.occurrence_count = 3
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing
    now = datetime.datetime.now(datetime.UTC)

    error, was_upserted = upsert_error(
        db,
        tenant_id=uuid.uuid4(),
        error_type="TimeoutError",
        message="Request timed out",
        stack_trace=None,
        service="api",
        component=None,
        severity="error",
        fingerprint=None,
        error_metadata=None,
        now=now,
    )

    db.add.assert_not_called()
    assert was_upserted is True
    assert error.occurrence_count == 4
    assert error.last_seen_at == now


@pytest.mark.unit
def test_upsert_error_uses_provided_fingerprint() -> None:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    custom_fp = "a" * 64
    now = datetime.datetime.now(datetime.UTC)

    with patch("app.services.error_ingestion.compute_fingerprint") as mock_fp:
        upsert_error(
            db,
            tenant_id=uuid.uuid4(),
            error_type="E",
            message="m",
            stack_trace=None,
            service=None,
            component=None,
            severity="error",
            fingerprint=custom_fp,
            error_metadata=None,
            now=now,
        )
        mock_fp.assert_not_called()
