# app/blog/router_admin.py -- admin-only blog CRUD. Static paths ("",
# "/reorder", "/images") are registered before the dynamic "/{post_id}"
# routes so they can never be shadowed.
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.blog import store
from app.blog.images import MAX_IMAGE_BYTES, save_image
from app.core.errors import UserError
from app.core.uploads import read_limited

router = APIRouter(prefix="/api/admin/blog", tags=["admin-blog"])


class BlogPostIn(BaseModel):
    title: str
    slug: str | None = None
    excerpt: str | None = None
    content: dict | None = None
    cover_image_url: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    is_featured: bool = False
    read_time_minutes: int | None = None
    status: Literal["DRAFT", "PUBLISHED"] = "DRAFT"


class BlogPostUpdateIn(BaseModel):
    title: str | None = None
    slug: str | None = None
    excerpt: str | None = None
    content: dict | None = None
    cover_image_url: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None
    read_time_minutes: int | None = None
    status: Literal["DRAFT", "PUBLISHED"] | None = None
    # For content migration (esgratings-web scripts/seed-blog.ts): imported
    # posts keep their original publish date rather than "today".
    published_at: datetime | None = None


class ReorderIn(BaseModel):
    post_ids: list[str]


def _check_slug(slug: str | None, exclude_id: str | None = None) -> None:
    if slug and not store.slug_available(slug, exclude_id=exclude_id):
        raise UserError("That slug is already used by another post.")


@router.get("")
def admin_list(admin: str = Depends(require_admin)):
    return store.list_all()


@router.post("", status_code=201)
def admin_create(payload: BlogPostIn, admin: str = Depends(require_admin)):
    _check_slug(payload.slug)
    fields = payload.model_dump()
    # Every new post starts as a draft whatever the client sent; publishing is
    # its own action.
    fields["status"] = store.STATUS_DRAFT
    return store.create_post(fields)


@router.put("/reorder")
def admin_reorder(payload: ReorderIn, admin: str = Depends(require_admin)):
    store.reorder(payload.post_ids)
    return {"ok": True}


@router.post("/images")
async def admin_upload_image(file: UploadFile = File(...), admin: str = Depends(require_admin)):
    data = await read_limited(file, MAX_IMAGE_BYTES, "The image must be 5 MB or smaller.")
    name = save_image(data)  # magic-byte checked: PNG / JPEG / WebP only
    return {"url": f"/api/blog/images/{name}", "key": name}


@router.get("/{post_id}")
def admin_get(post_id: str, admin: str = Depends(require_admin)):
    post = store.get_by_id(post_id)
    if not post:
        raise HTTPException(404, "Not found")
    return post


@router.put("/{post_id}")
def admin_update(post_id: str, payload: BlogPostUpdateIn, admin: str = Depends(require_admin)):
    fields = payload.model_dump(exclude_unset=True)
    if "title" in fields and not (fields["title"] or "").strip():
        raise UserError("The title can't be empty.")
    if "slug" in fields:
        if not fields["slug"]:
            raise UserError("The slug can't be empty.")
        _check_slug(fields["slug"], exclude_id=post_id)
    post = store.update_post(post_id, fields)
    if not post:
        raise HTTPException(404, "Not found")
    return post


@router.delete("/{post_id}")
def admin_delete(post_id: str, admin: str = Depends(require_admin)):
    if not store.delete_post(post_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.post("/{post_id}/publish")
def admin_publish(post_id: str, admin: str = Depends(require_admin)):
    post = store.set_status(post_id, store.STATUS_PUBLISHED)
    if not post:
        raise HTTPException(404, "Not found")
    return post


@router.post("/{post_id}/unpublish")
def admin_unpublish(post_id: str, admin: str = Depends(require_admin)):
    post = store.set_status(post_id, store.STATUS_DRAFT)
    if not post:
        raise HTTPException(404, "Not found")
    return post
