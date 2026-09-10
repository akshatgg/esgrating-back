import hashlib, secrets, time
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import UserError

_READ_CHUNK = 1024 * 1024


def _dir(kind: str) -> Path:
    d = (settings.upload_dir / kind).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


async def read_limited(upload: UploadFile, limit: int, too_large: str) -> bytes:
    """Read an upload into memory, but never more than limit + 1 bytes: raise
    UserError(too_large) -- the calling route's own too-large message -- as soon as the
    file is known to exceed the limit."""
    if upload.size is not None and upload.size > limit:
        raise UserError(too_large)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(_READ_CHUNK, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise UserError(too_large)
    return b"".join(chunks)


def save_upload(kind: str, data: bytes, ext: str) -> tuple[str, str]:
    name = f"{int(time.time())}_{secrets.token_hex(8)}.{ext}"
    (_dir(kind) / name).write_bytes(data)
    return name, hashlib.sha256(data).hexdigest()


def upload_path(kind: str, stored_name: str) -> Path:
    base = _dir(kind)
    p = (base / Path(stored_name).name).resolve()
    if p.parent != base or not p.is_file():
        raise FileNotFoundError(stored_name)
    return p
