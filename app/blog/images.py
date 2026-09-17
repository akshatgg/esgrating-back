# app/blog/images.py -- blog cover/content images, stored under
# UPLOAD_DIR/blog-images/. Unlike report logos (private, admin-only), these
# are served publicly by app/blog/router_public.py: every published post's
# images must load without an admin session.
from pathlib import Path

from app.core.errors import UserError
from app.core.uploads import save_upload, upload_path

IMAGE_KIND = "blog-images"
MAX_IMAGE_BYTES = 5 * 1024 * 1024

MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def sniff_image(data: bytes) -> str:
    """Extension from the file's magic bytes. PNG, JPEG and WebP only; SVG
    (script-capable) and everything else is rejected whatever the filename or
    Content-Type says."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise UserError("The image must be a PNG, JPEG or WebP file.")


def save_image(data: bytes) -> str:
    if not data:
        raise UserError("Please choose an image.")
    ext = sniff_image(data)
    name, _sha = save_upload(IMAGE_KIND, data, ext)
    return name


def image_path(name: str) -> Path:
    """Raises FileNotFoundError when missing (or when the name tries to escape the dir)."""
    return upload_path(IMAGE_KIND, name)


def image_media_type(name: str) -> str:
    return MEDIA_TYPES.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
