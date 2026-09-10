from datetime import datetime, timedelta, timezone

VALID = {"name": "Asha Rao", "email": "asha@example.com", "number": "9876543210", "message": "Please call me back."}


def test_contact_requires_name_email_message(client):
    for missing in ("name", "email", "message"):
        payload = dict(VALID)
        payload[missing] = ""
        resp = client.post("/api/contact", json=payload)
        assert resp.status_code == 422, missing
        assert resp.json()["detail"] == "Please fill in all required fields."


def test_contact_number_not_required(client, db):
    payload = dict(VALID)
    payload["number"] = ""
    resp = client.post("/api/contact", json=payload)
    assert resp.status_code == 201
    assert db.contact_messages.count_documents({}) == 1


def test_contact_invalid_email(client):
    payload = dict(VALID, email="not-an-email")
    resp = client.post("/api/contact", json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Please enter a valid email"


def test_contact_stores_and_returns_ok(client, db):
    resp = client.post("/api/contact", json=VALID)
    assert resp.status_code == 201
    assert resp.json() == {"ok": True}
    doc = db.contact_messages.find_one({})
    assert doc["name"] == "Asha Rao"
    assert doc["email"] == "asha@example.com"
    assert doc["number"] == "9876543210"
    assert doc["message"] == "Please call me back."
    assert "created_at" in doc
    assert "submit_ip" in doc


def test_contact_rate_limited_after_five_per_hour(client, db):
    ip = "testclient"
    now = datetime.now(timezone.utc)
    for i in range(5):
        db.contact_messages.insert_one({
            "name": "X", "email": "x@example.com", "number": "", "message": "hi",
            "submit_ip": ip, "created_at": now - timedelta(minutes=i),
        })
    resp = client.post("/api/contact", json=VALID)
    assert resp.status_code == 429


def test_contact_old_submissions_dont_count_toward_limit(client, db):
    ip = "testclient"
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    for i in range(5):
        db.contact_messages.insert_one({
            "name": "X", "email": "x@example.com", "number": "", "message": "hi",
            "submit_ip": ip, "created_at": old,
        })
    resp = client.post("/api/contact", json=VALID)
    assert resp.status_code == 201


def test_admin_contacts_requires_auth(client):
    assert client.get("/api/admin/contacts").status_code == 401


def test_admin_contacts_newest_first_paginated(admin_client, db):
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.contact_messages.insert_one({
            "name": f"Person {i}", "email": "x@example.com", "number": "", "message": "hi",
            "submit_ip": "1.2.3.4", "created_at": now + timedelta(seconds=i),
        })
    resp = admin_client.get("/api/admin/contacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert [i["name"] for i in body["items"]] == ["Person 2", "Person 1", "Person 0"]
