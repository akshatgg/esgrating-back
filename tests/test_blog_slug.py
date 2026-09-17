from app.blog.slug import slugify
from app.core.config import Settings


def test_slugify_basic():
    assert slugify("ESG Ratings: Future-Proof Your Business!") == "esg-ratings-future-proof-your-business"


def test_slugify_collapses_repeated_separators():
    assert slugify("Hello   World---Again") == "hello-world-again"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify("  -- Hello --  ") == "hello"


def test_slugify_empty_falls_back_to_post():
    assert slugify("???") == "post"


def test_blog_author_defaults():
    # _env_file=None: ignore any local .env so the defaults are what's tested.
    s = Settings(_env_file=None)
    assert s.blog_author_name == "Chawla"
    assert s.blog_author_title == ""
    assert s.blog_author_avatar_url == ""
