# app/blog/router_public.py -- public blog reads. Every response's byline is
# injected from config (never stored per-post) -- see
# docs/specs/2026-09-11-blog-feature-design.md.
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.blog import store
from app.blog.images import image_media_type, image_path
from app.core.config import settings

router = APIRouter(prefix="/api/blog", tags=["blog"])


def _author() -> dict:
    return {
        "name": settings.blog_author_name,
        "title": settings.blog_author_title or None,
        "avatar_url": settings.blog_author_avatar_url or None,
    }


def _with_author(doc: dict) -> dict:
    out = dict(doc)
    out["author"] = _author()
    return out


# Static path first, as in app/ratings/router.py and app/reports/router.py.
@router.get("/images/{name}")
def get_image(name: str):
    try:
        path = image_path(name)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    # Stored names are random and never reused, so the bytes behind a URL never change.
    return FileResponse(path, media_type=image_media_type(name), headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
    })


@router.get("")
def list_posts():
    return [_with_author(p) for p in store.list_published()]


@router.get("/{slug}")
def get_post(slug: str):
    post = store.get_by_slug(slug, published_only=True)
    if not post:
        raise HTTPException(404, "Not found")
    return _with_author(post)


@router.get("/{slug}/related")
def related_posts(slug: str):
    return [_with_author(p) for p in store.list_related(slug)]
