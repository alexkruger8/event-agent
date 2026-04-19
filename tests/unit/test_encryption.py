import pytest

from app.security.encryption import (
    EncryptionConfigurationError,
    decrypt_secret,
    encrypt_secret,
    generate_encryption_key,
)


@pytest.mark.unit
def test_encrypt_decrypt_secret_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.security.encryption.settings.kafka_credential_encryption_key",
        generate_encryption_key(),
    )

    encrypted = encrypt_secret("redpanda-password")

    assert encrypted != "redpanda-password"
    assert decrypt_secret(encrypted) == "redpanda-password"


@pytest.mark.unit
def test_encrypt_secret_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.security.encryption.settings.kafka_credential_encryption_key", None)

    with pytest.raises(EncryptionConfigurationError):
        encrypt_secret("redpanda-password")
