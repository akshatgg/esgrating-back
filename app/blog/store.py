# app/blog/store.py -- blog_posts collection. Global (not workspace-scoped);
# `id` is an app-assigned uuid4 string (not the Mongo _id), the same pattern
# as esg_ratings' s_no (app/ratings/store.py). The byline is never stored
# here -- app/blog/router_public.py injects it from config at response time
# (docs/specs/2026-09-11-blog-feature-design.md).
import uuid
from datetime import datetime, timezone

from app.blog.slug import slugify
from app.core.db import get_db

STATUS_DRAFT = "DRAFT"
STATUS_PUBLISHED = "PUBLISHED"


def blog_collection():
    return get_db()["blog_posts"]


def ensure_indexes() -> None:
    blog_collection().create_index("id", unique=True)
    blog_collection().create_index("slug", unique=True)


def _slug_taken(slug: str, exclude_id: str | None) -> bool:
    query: dict = {"slug": slug}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    return blog_collection().find_one(query) is not None


def slug_available(slug: str, exclude_id: str | None = None) -> bool:
    return not _slug_taken(slug, exclude_id)


def generate_unique_slug(title: str, exclude_id: str | None = None) -> str:
    base = slugify(title)
    slug = base
    n = 2
    while _slug_taken(slug, exclude_id):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _next_display_order() -> int:
    last = blog_collection().find_one(sort=[("display_order", -1)])
    return (last["display_order"] + 1) if last else 0


def create_post(fields: dict) -> dict:
    doc = dict(fields)
    doc["id"] = str(uuid.uuid4())
    if not doc.get("slug"):
        doc["slug"] = generate_unique_slug(doc["title"])
    doc.setdefault("display_order", _next_display_order())
    doc["created_at"] = datetime.now(timezone.utc)
    doc["updated_at"] = None
    doc.setdefault("published_at", None)
    blog_collection().insert_one(doc)
    doc.pop("_id", None)
    return doc


def get_by_id(post_id: str) -> dict | None:
    return blog_collection().find_one({"id": post_id}, {"_id": 0})


def get_by_slug(slug: str, published_only: bool) -> dict | None:
    query: dict = {"slug": slug}
    if published_only:
        query["status"] = STATUS_PUBLISHED
    return blog_collection().find_one(query, {"_id": 0})


def list_published() -> list[dict]:
    return list(
        blog_collection()
        .find({"status": STATUS_PUBLISHED}, {"_id": 0})
        .sort([("is_featured", -1), ("display_order", 1), ("published_at", -1)])
    )


def list_all() -> list[dict]:
    return list(blog_collection().find({}, {"_id": 0}).sort("display_order", 1))


def list_related(slug: str, limit: int = 3) -> list[dict]:
    post = get_by_slug(slug, published_only=False)
    if not post or not post.get("category"):
        return []
    return list(
        blog_collection()
        .find(
            {"status": STATUS_PUBLISHED, "category": post["category"], "slug": {"$ne": slug}},
            {"_id": 0},
        )
        .sort("published_at", -1)
        .limit(limit)
    )


def update_post(post_id: str, fields: dict) -> dict | None:
    """A raw $set of whatever's in `fields` -- the caller (app/blog/router_admin.py)
    decides what's allowed in. Slug is left untouched unless the caller
    includes it: editing the title never silently changes a post's URL."""
    fields = dict(fields)
    fields["updated_at"] = datetime.now(timezone.utc)
    result = blog_collection().update_one({"id": post_id}, {"$set": fields})
    if result.matched_count == 0:
        return None
    return get_by_id(post_id)


def delete_post(post_id: str) -> bool:
    result = blog_collection().delete_one({"id": post_id})
    return result.deleted_count > 0


def set_status(post_id: str, status: str) -> dict | None:
    """Publishing stamps published_at the first time only: unpublishing and
    republishing keeps the original date."""
    update: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == STATUS_PUBLISHED:
        post = get_by_id(post_id)
        if post and not post.get("published_at"):
            update["published_at"] = datetime.now(timezone.utc)
    result = blog_collection().update_one({"id": post_id}, {"$set": update})
    if result.matched_count == 0:
        return None
    return get_by_id(post_id)


def reorder(post_ids: list[str]) -> None:
    for i, post_id in enumerate(post_ids):
        blog_collection().update_one({"id": post_id}, {"$set": {"display_order": i}})
