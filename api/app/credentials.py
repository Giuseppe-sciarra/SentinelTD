"""Cifratura delle password salvate nel pannello (accessi dei server).

Chiave Fernet ricavata da JWT_SECRET: se il segreto cambia, le password salvate prima non si
leggono piu' (vanno riscritte). Nel database c'e' solo il testo cifrato."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    raw = hashlib.sha256(("sentinel-credentials:" + settings.JWT_SECRET).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode() if plain else ""


def decrypt(token: str) -> str | None:
    """Password in chiaro; None se non si riesce a leggere (segreto cambiato)."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
