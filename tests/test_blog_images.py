import pytest

from app.blog.images import image_media_type, image_path, save_image, sniff_image
from app.core.errors import UserError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG = b"\xff\xd8\xff" + b"\x00" * 20
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20


def test_sniff_image_accepts_png_jpeg_webp():
    assert sniff_image(PNG) == "png"
    assert sniff_image(JPEG) == "jpg"
    assert sniff_image(WEBP) == "webp"


def test_sniff_image_rejects_svg():
    with pytest.raises(UserError):
        sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")


def test_save_image_rejects_empty(upload_dir):
    with pytest.raises(UserError):
        save_image(b"")


def test_save_image_writes_file(upload_dir):
    name = save_image(PNG)
    assert name.endswith(".png")
    assert image_path(name).read_bytes() == PNG


def test_image_path_missing_or_escaping_raises(upload_dir):
    with pytest.raises(FileNotFoundError):
        image_path("does-not-exist.png")
    (upload_dir / "secret.txt").write_text("x")
    with pytest.raises(FileNotFoundError):
        image_path("../secret.txt")


def test_image_media_type():
    assert image_media_type("x.png") == "image/png"
    assert image_media_type("x.jpg") == "image/jpeg"
    assert image_media_type("x.webp") == "image/webp"
    assert image_media_type("x.bin") == "application/octet-stream"
