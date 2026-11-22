"""Field-level crypto for KYC data.

- encrypt/decrypt: AES-256-GCM (nonce prepended to ciphertext, base64 stored).
  Key comes from DIRECTHOME_KYC_ENC_KEY (base64, 32 bytes); if unset it is
  derived from DIRECTHOME_SECRET_KEY so dev works out of the box — set a real
  key in production (KMS-ready seam: swap _enc_key() only).
- hmac_id: HMAC-SHA256 dedup hash for PAN etc. Pepper from
  DIRECTHOME_KYC_HMAC_KEY (falls back to derived-from-secret in dev).
  NEVER store raw identifiers — only these hashes + encrypted copies.
"""
import base64
import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings


def _b64key(raw: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(raw)
    except Exception:
        return hashlib.sha256(raw.encode()).digest()


@lru_cache
def _enc_key() -> bytes:
    if settings.kyc_enc_key:
        key = _b64key(settings.kyc_enc_key)
    else:  # dev fallback: derive from app secret (stable, but set a real key in prod)
        key = hashlib.sha256((settings.secret_key + "|kyc-enc").encode()).digest()
    if len(key) != 32:
        key = hashlib.sha256(key).digest()
    return key


@lru_cache
def _pepper() -> bytes:
    raw = settings.kyc_hmac_key or (settings.secret_key + "|kyc-hmac")
    return raw.encode()


def encrypt(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    nonce = os.urandom(12)
    ct = AESGCM(_enc_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    raw = base64.urlsafe_b64decode(token.encode())
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_enc_key()).decrypt(nonce, ct, None).decode()


def encrypt_bytes(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_enc_key()).encrypt(nonce, data, None)


def decrypt_bytes(blob: bytes) -> bytes:
    return AESGCM(_enc_key()).decrypt(blob[:12], blob[12:], None)


def hmac_id(value: str) -> str:
    """Deterministic keyed hash for dedup (PAN hash). Not reversible."""
    return hmac.new(_pepper(), value.strip().upper().encode(), hashlib.sha256).hexdigest()


def mask_pan(pan: str | None) -> str | None:
    """ABCDE****F — reviewers see masked PAN by default."""
    if not pan or len(pan) != 10:
        return None
    return pan[:5] + "****" + pan[-1]
