import hashlib, secrets, time
from pathlib import Path
from app.core.config import settings


def _dir(kind: str) -> Path:
    d = (settings.upload_dir / kind).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


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
