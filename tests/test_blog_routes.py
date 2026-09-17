from app.blog import store

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def _payload(**overrides):
    payload = {
        "title": "ESG Ratings: Future-Proof Your Business",
        "excerpt": "Why ESG matters.",
        "content": {"type": "doc", "content": []},
        "category": "ESG",
        "tags": ["ESG"],
        "is_featured": False,
        "read_time_minutes": 4,
    }
    payload.update(overrides)
    return payload


def _published(**overrides):
    post = store.create_post({**_payload(), "status": store.STATUS_DRAFT, **overrides})
    return store.set_status(post["id"], store.STATUS_PUBLISHED)


# --- public -------------------------------------------------------------------------

def test_public_list_empty(client):
    resp = client.get("/api/blog")
    assert resp.status_code == 200
    assert resp.json() == []


def test_public_list_has_config_byline_and_hides_drafts(client, db):
    _published()
    store.create_post({"title": "Draft only", "status": store.STATUS_DRAFT})
    body = client.get("/api/blog").json()
    assert len(body) == 1
    assert body[0]["author"] == {"name": "Chawla", "title": None, "avatar_url": None}


def test_public_byline_follows_settings(client, db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "blog_author_name", "S. Chawla")
    post = _published()
    assert client.get(f"/api/blog/{post['slug']}").json()["author"]["name"] == "S. Chawla"


def test_public_get_by_slug(client, db):
    post = _published()
    resp = client.get(f"/api/blog/{post['slug']}")
    assert resp.status_code == 200
    assert resp.json()["title"] == post["title"]


def test_public_get_404_for_draft_and_missing(client, db):
    draft = store.create_post({"title": "Draft only", "status": store.STATUS_DRAFT})
    assert client.get(f"/api/blog/{draft['slug']}").status_code == 404
    assert client.get("/api/blog/does-not-exist").status_code == 404


def test_public_related(client, db):
    main = _published(title="Main")
    _published(title="Related")
    resp = client.get(f"/api/blog/{main['slug']}/related")
    assert resp.status_code == 200
    assert [p["title"] for p in resp.json()] == ["Related"]


def test_public_image_404_missing(client):
    assert client.get("/api/blog/images/does-not-exist.png").status_code == 404


# --- admin --------------------------------------------------------------------------

def test_admin_routes_require_auth(client):
    assert client.get("/api/admin/blog").status_code == 401
    assert client.post("/api/admin/blog", json=_payload()).status_code == 401
    assert client.put("/api/admin/blog/x", json={"title": "x"}).status_code == 401
    assert client.delete("/api/admin/blog/x").status_code == 401
    assert client.post("/api/admin/blog/x/publish").status_code == 401
    assert client.put("/api/admin/blog/reorder", json={"post_ids": []}).status_code == 401
    resp = client.post("/api/admin/blog/images", files={"file": ("x.png", PNG, "image/png")})
    assert resp.status_code == 401


def test_admin_create_forces_draft_and_ignores_author(admin_client, db):
    resp = admin_client.post("/api/admin/blog", json=_payload(status="PUBLISHED", author="Someone"))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "DRAFT"
    assert body["slug"] == "esg-ratings-future-proof-your-business"
    assert "author" not in db.blog_posts.find_one({"id": body["id"]})


def test_admin_create_rejects_taken_slug(admin_client, db):
    assert admin_client.post("/api/admin/blog", json=_payload(slug="taken")).status_code == 201
    resp = admin_client.post("/api/admin/blog", json=_payload(slug="taken"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "That slug is already used by another post."


def test_admin_list_includes_drafts(admin_client, db):
    admin_client.post("/api/admin/blog", json=_payload())
    body = admin_client.get("/api/admin/blog").json()
    assert len(body) == 1
    assert body[0]["status"] == "DRAFT"


def test_admin_get_update_delete(admin_client, db):
    created = admin_client.post("/api/admin/blog", json=_payload()).json()
    assert admin_client.get(f"/api/admin/blog/{created['id']}").json()["title"] == created["title"]

    updated = admin_client.put(f"/api/admin/blog/{created['id']}", json={"title": "New Title"}).json()
    assert updated["title"] == "New Title"
    assert updated["slug"] == created["slug"]

    assert admin_client.delete(f"/api/admin/blog/{created['id']}").status_code == 200
    assert admin_client.get(f"/api/admin/blog/{created['id']}").status_code == 404


def test_admin_missing_404s(admin_client):
    assert admin_client.get("/api/admin/blog/nope").status_code == 404
    assert admin_client.put("/api/admin/blog/nope", json={"title": "x"}).status_code == 404
    assert admin_client.delete("/api/admin/blog/nope").status_code == 404
    assert admin_client.post("/api/admin/blog/nope/publish").status_code == 404
    assert admin_client.post("/api/admin/blog/nope/unpublish").status_code == 404


def test_admin_update_rejects_empty_title_or_slug_and_taken_slug(admin_client, db):
    a = admin_client.post("/api/admin/blog", json=_payload(title="A")).json()
    b = admin_client.post("/api/admin/blog", json=_payload(title="B")).json()
    assert admin_client.put(f"/api/admin/blog/{a['id']}", json={"title": "  "}).status_code == 422
    assert admin_client.put(f"/api/admin/blog/{a['id']}", json={"slug": ""}).status_code == 422
    assert admin_client.put(f"/api/admin/blog/{a['id']}", json={"slug": b["slug"]}).status_code == 422
    # Re-saving its own slug is fine.
    assert admin_client.put(f"/api/admin/blog/{a['id']}", json={"slug": a["slug"]}).status_code == 200


def test_admin_update_can_set_published_at_for_migration(admin_client, db):
    created = admin_client.post("/api/admin/blog", json=_payload()).json()
    resp = admin_client.put(
        f"/api/admin/blog/{created['id']}",
        json={"status": "PUBLISHED", "published_at": "2025-05-04T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["published_at"].startswith("2025-05-04")
    public = admin_client.get(f"/api/blog/{created['slug']}").json()
    assert public["published_at"].startswith("2025-05-04")


def test_admin_publish_and_unpublish(admin_client, db):
    created = admin_client.post("/api/admin/blog", json=_payload()).json()
    published = admin_client.post(f"/api/admin/blog/{created['id']}/publish").json()
    assert published["status"] == "PUBLISHED"
    assert published["published_at"] is not None
    assert admin_client.get(f"/api/blog/{created['slug']}").status_code == 200

    unpublished = admin_client.post(f"/api/admin/blog/{created['id']}/unpublish").json()
    assert unpublished["status"] == "DRAFT"
    assert unpublished["published_at"] == published["published_at"]
    assert admin_client.get(f"/api/blog/{created['slug']}").status_code == 404


def test_admin_reorder(admin_client, db):
    a = admin_client.post("/api/admin/blog", json=_payload(title="A")).json()
    b = admin_client.post("/api/admin/blog", json=_payload(title="B")).json()
    resp = admin_client.put("/api/admin/blog/reorder", json={"post_ids": [b["id"], a["id"]]})
    assert resp.status_code == 200
    assert [p["id"] for p in admin_client.get("/api/admin/blog").json()] == [b["id"], a["id"]]


def test_admin_upload_image_rejects_non_image(admin_client):
    resp = admin_client.post("/api/admin/blog/images", files={"file": ("x.png", b"not an image", "image/png")})
    assert resp.status_code == 422


def test_admin_upload_image_then_public_serves_it(admin_client):
    resp = admin_client.post("/api/admin/blog/images", files={"file": ("x.png", PNG, "image/png")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == f"/api/blog/images/{body['key']}"

    image = admin_client.get(body["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG
