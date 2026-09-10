# app/reports/logo.py -- custom report logos, stored under UPLOAD_DIR/report-logos/.
# Kept free of submission/pipeline imports so the analysis runners can call
# delete_logo_file() without an import cycle.
from pathlib import Path

from app.core.errors import UserError
from app.core.uploads import save_upload, upload_path

LOGO_KIND = "report-logos"
MAX_LOGO_BYTES = 1024 * 1024

MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def sniff_logo(data: bytes) -> str:
    """Extension from the file's magic bytes. PNG, JPEG and WebP only; SVG (script-capable)
    and everything else is rejected whatever the filename or Content-Type says."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise UserError("The logo must be a PNG, JPEG or WebP image.")


def save_logo(data: bytes) -> str:
    if not data:
        raise UserError("Please choose a logo image.")
    ext = sniff_logo(data)
    name, _sha = save_upload(LOGO_KIND, data, ext)
    return name


def logo_path(name: str) -> Path:
    """Raises FileNotFoundError when missing (or when the name tries to escape the dir)."""
    return upload_path(LOGO_KIND, name)


def logo_media_type(name: str) -> str:
    return MEDIA_TYPES.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def delete_logo_file(name: str | None) -> None:
    if not name:
        return
    try:
        logo_path(name).unlink()
    except FileNotFoundError:
        pass
