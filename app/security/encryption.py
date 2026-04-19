from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class EncryptionConfigurationError(RuntimeError):
    """Raised when encrypted tenant credentials cannot be processed."""


def generate_encryption_key() -> str:
    """Return a new Fernet key suitable for KAFKA_CREDENTIAL_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    key = settings.kafka_credential_encryption_key
    if not key:
        raise EncryptionConfigurationError(
            "KAFKA_CREDENTIAL_ENCRYPTION_KEY must be set to store Kafka passwords."
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
