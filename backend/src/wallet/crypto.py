"""Symmetric encryption for managed-wallet private keys.

Uses Fernet (AES-128-CBC + HMAC-SHA256). The master key lives in
`settings.master_encryption_key`. For the personal-use phase this can be a
random Fernet key in `.env`. For the SaaS phase, swap to AWS KMS / GCP KMS /
HashiCorp Vault — never raw keys in env."""
from cryptography.fernet import Fernet, InvalidToken

from src.config.settings import settings


class CryptoError(RuntimeError):
    """Raised when encryption or decryption fails, including missing master key."""


def _fernet() -> Fernet:
    key = settings.master_encryption_key
    if not key:
        raise CryptoError(
            "MASTER_ENCRYPTION_KEY is not set. Generate one with "
            "`.venv/Scripts/python -m src.wallet.crypto generate` and put it in .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise CryptoError(
            f"MASTER_ENCRYPTION_KEY is malformed: {e}. "
            "It must be a URL-safe base64-encoded 32-byte key."
        ) from e


def encrypt(plaintext: bytes) -> bytes:
    return _fernet().encrypt(plaintext)


def decrypt(ciphertext: bytes) -> bytes:
    try:
        return _fernet().decrypt(ciphertext)
    except InvalidToken as e:
        raise CryptoError(
            "Decryption failed — likely the master key changed since this row was written."
        ) from e


def generate_master_key() -> str:
    """Generate a fresh Fernet key. Returned as URL-safe base64 ASCII string."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "generate":
        print(generate_master_key())
    else:
        print(
            "Usage: python -m src.wallet.crypto generate\n"
            "Prints a fresh URL-safe base64 Fernet key. Put it in .env as MASTER_ENCRYPTION_KEY."
        )
        sys.exit(1)
