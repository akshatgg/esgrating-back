from app.blog import store


def _fields(**overrides):
    fields = {
        "title": "ESG Ratings: Future-Proof Your Business",
        "excerpt": "Why ESG matters.",
        "content": {"type": "doc", "content": []},
        "cover_image_url": None,
        "category": "ESG",
        "tags": ["ESG", "BRSR"],
        "is_featured": False,
        "read_time_minutes": 4,
        "status": store.STATUS_DRAFT,
    }
    fields.update(overrides)
    return fields


def test_create_post_generates_id_slug_and_order(db):
    first = store.create_post(_fields())
    second = store.create_post(_fields(title="Another"))
    assert first["id"] and first["id"] != second["id"]
    assert first["slug"] == "esg-ratings-future-proof-your-business"
    assert first["created_at"] is not None
    assert first["updated_at"] is None
    assert first["published_at"] is None
    assert (first["display_order"], second["display_order"]) == (0, 1)
    assert "_id" not in first


def test_create_post_dedupes_slug_collision(db):
    store.create_post(_fields())
    assert store.create_post(_fields())["slug"] == "esg-ratings-future-proof-your-business-2"
    assert store.create_post(_fields())["slug"] == "esg-ratings-future-proof-your-business-3"


def test_create_post_honors_explicit_slug(db):
    assert store.create_post(_fields(slug="custom-slug"))["slug"] == "custom-slug"


def test_get_by_slug_published_only_hides_drafts(db):
    post = store.create_post(_fields())
    assert store.get_by_slug(post["slug"], published_only=True) is None
    assert store.get_by_slug(post["slug"], published_only=False)["id"] == post["id"]
    store.set_status(post["id"], store.STATUS_PUBLISHED)
    assert store.get_by_slug(post["slug"], published_only=True)["id"] == post["id"]


def test_list_published_featured_first_drafts_excluded(db):
    a = store.create_post(_fields(title="A"))
    b = store.create_post(_fields(title="B", is_featured=True))
    c = store.create_post(_fields(title="C"))  # stays a draft
    store.set_status(a["id"], store.STATUS_PUBLISHED)
    store.set_status(b["id"], store.STATUS_PUBLISHED)
    # Featured first even though B sits after A in display_order; the draft C is left out.
    assert [p["slug"] for p in store.list_published()] == [b["slug"], a["slug"]]
    assert c["id"] in [p["id"] for p in store.list_all()]


def test_update_post_leaves_slug_alone_on_title_edit(db):
    post = store.create_post(_fields())
    updated = store.update_post(post["id"], {"title": "A Brand New Title"})
    assert updated["slug"] == post["slug"]
    assert updated["title"] == "A Brand New Title"
    assert updated["updated_at"] is not None


def test_update_and_delete_missing(db):
    assert store.update_post("nope", {"title": "x"}) is None
    assert store.delete_post("nope") is False
    assert store.set_status("nope", store.STATUS_PUBLISHED) is None


def test_delete_post(db):
    post = store.create_post(_fields())
    assert store.delete_post(post["id"]) is True
    assert store.get_by_id(post["id"]) is None


def test_publish_stamps_published_at_once(db):
    post = store.create_post(_fields())
    published = store.set_status(post["id"], store.STATUS_PUBLISHED)
    first = published["published_at"]
    assert published["status"] == store.STATUS_PUBLISHED and first is not None
    unpublished = store.set_status(post["id"], store.STATUS_DRAFT)
    assert unpublished["status"] == store.STATUS_DRAFT
    assert unpublished["published_at"] == first
    assert store.set_status(post["id"], store.STATUS_PUBLISHED)["published_at"] == first


def test_list_related_same_category_published_not_self(db):
    main = store.create_post(_fields(title="Main"))
    same = store.create_post(_fields(title="Same category"))
    other = store.create_post(_fields(title="Other category", category="BFSI"))
    draft = store.create_post(_fields(title="Draft same category"))
    for p in (main, same, other):
        store.set_status(p["id"], store.STATUS_PUBLISHED)
    assert [p["slug"] for p in store.list_related(main["slug"])] == [same["slug"]]
    assert draft["slug"] not in [p["slug"] for p in store.list_related(main["slug"])]
    assert store.list_related("missing") == []


def test_reorder_sets_display_order_by_position(db):
    a, b, c = (store.create_post(_fields(title=t)) for t in "ABC")
    store.reorder([c["id"], a["id"], b["id"]])
    assert [p["id"] for p in store.list_all()] == [c["id"], a["id"], b["id"]]


def test_slug_available(db):
    post = store.create_post(_fields())
    assert store.slug_available(post["slug"]) is False
    assert store.slug_available(post["slug"], exclude_id=post["id"]) is True
    assert store.slug_available("totally-unused") is True
