# app/blog/slug.py -- URL-safe slug generation for blog posts.
import re

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Lowercase, hyphen-separated. Falls back to "post" if the title has no
    alphanumeric characters at all -- an empty slug can never be stored
    (app/blog/store.py enforces a unique index on slug)."""
    slug = _NON_SLUG_CHARS.sub("-", title.strip().lower()).strip("-")
    return slug or "post"
