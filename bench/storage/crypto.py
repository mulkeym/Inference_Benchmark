import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet


def load_or_create_secret(data_dir: Path, secret_key: str | None = None) -> bytes:
    if secret_key:
        return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    path = data_dir / ".secret"
    if path.exists():
        return path.read_bytes()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def encrypt(secret: bytes, plaintext: str) -> str:
    return Fernet(secret).encrypt(plaintext.encode()).decode()


def decrypt(secret: bytes, token: str) -> str:
    return Fernet(secret).decrypt(token.encode()).decode()
