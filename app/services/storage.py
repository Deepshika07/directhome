"""Document storage abstraction.

Phase 1: `LocalEncryptedStorage` — every blob is AES-GCM encrypted before it
touches the Lightsail disk (key from app.core.crypto). Swap in an
S3-compatible backend (Lightsail bucket / AWS S3 / R2) later by implementing
`DocStorage` and changing `get_storage()` — callers never change.

Hooks reserved for later: virus scan (ClamAV), EXIF strip (images).
"""
import hashlib
import mimetypes
import re
import secrets
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.crypto import decrypt_bytes, encrypt_bytes
from app.core.errors import bad_request

ALLOWED_MIMES = {
    "image/jpeg", "image/png", "image/webp", "application/pdf",
}
# magic-byte signatures for server-side MIME sniffing
_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),
    (b"%PDF-", "application/pdf"),
]

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]")


class DocStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalEncryptedStorage:
    """Writes encrypted blobs under settings.kyc_storage_dir."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.kyc_storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise bad_request("BAD_KEY", "Invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(encrypt_bytes(data))

    def get(self, key: str) -> bytes:
        return decrypt_bytes(self._path(key).read_bytes())

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:
    """Reserved: S3-compatible backend (Lightsail bucket). Configure
    endpoint/bucket + keys via env, add boto3 to requirements, and return
    this from get_storage()."""

    def __init__(self):
        raise NotImplementedError(
            "S3 backend not configured — set up a Lightsail bucket first"
        )


def get_storage() -> DocStorage:
    return LocalEncryptedStorage()


def new_storage_key(owner_id: int, filename: str) -> str:
    safe = _SAFE_NAME.sub("_", Path(filename).name)[:80] or "doc"
    ext = Path(safe).suffix or mimetypes.guess_extension("application/octet-stream") or ".bin"
    return f"u{owner_id}/{secrets.token_hex(12)}{ext}.enc"


def sniff_mime(data: bytes, declared: str) -> str:
    """Server-side MIME check against magic bytes."""
    for sig, mime in _MAGIC:
        if data.startswith(sig):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    raise bad_request("BAD_FILE_TYPE", "Unsupported or corrupt file")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
