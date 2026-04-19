from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

# Resolved once at startup by ensure_encryption_key(); used as fallback when the
# env var is not set.
_runtime_key: str | None = None


class EncryptionConfigurationError(RuntimeError):
    """Raised when encrypted tenant credentials cannot be processed."""


def generate_encryption_key() -> str:
    """Return a new Fernet key suitable for KAFKA_CREDENTIAL_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")


def ensure_encryption_key(db: Session) -> None:
    """Called once at startup.

    Resolution order:
    1. KAFKA_CREDENTIAL_ENCRYPTION_KEY env var (explicit override)
    2. Key stored in the system_config table (persisted from a previous run)
    3. Auto-generate a new key and persist it for future runs
    """
    global _runtime_key

    if settings.kafka_credential_encryption_key:
        _runtime_key = settings.kafka_credential_encryption_key
        return

    row = db.execute(
        text("SELECT value FROM system_config WHERE key = 'kafka_credential_encryption_key'")
    ).fetchone()

    if row:
        _runtime_key = row[0]
    else:
        new_key = generate_encryption_key()
        db.execute(
            text(
                "INSERT INTO system_config (key, value) VALUES ('kafka_credential_encryption_key', :v)"
            ),
            {"v": new_key},
        )
        db.commit()
        _runtime_key = new_key


def _fernet() -> Fernet:
    key = settings.kafka_credential_encryption_key or _runtime_key
    if not key:
        raise EncryptionConfigurationError(
            "Encryption key not initialised. This is a bug — ensure_encryption_key() should have been called at startup."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise EncryptionConfigurationError(
            "KAFKA_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionConfigurationError("Stored Kafka password could not be decrypted.") from exc
