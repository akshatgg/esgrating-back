import bcrypt
from app.auth.service import authenticate, hash_password, verify_password, limiter


def test_verify_php_2y_hash():
    php_style = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode().replace("$2b$", "$2y$", 1)
    assert verify_password("secret", php_style)
    assert not verify_password("nope", php_style)


def test_login_flow(client, db):
    limiter._hits.clear()
    db.admin_users.insert_one({"username": "admin", "password": hash_password("admin123")})
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"username": "", "password": ""})
    assert r.status_code == 422 and r.json()["detail"] == "Please fill all fields!"
    r = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong username or password!"
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200 and "esg_session" in r.cookies
    assert client.get("/api/auth/me").json() == {"username": "admin"}
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_login_lockout(client, db):
    limiter._hits.clear()
    db.admin_users.insert_one({"username": "admin", "password": hash_password("admin123")})
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 429
    assert r.json()["detail"] == "Too many failed attempts — try again in 15 minutes."


def test_me_rejects_tampered_or_garbage_cookie(client):
    client.cookies.set("esg_session", "garbage-not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401

    from app.auth.service import make_session
    token = make_session("admin")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    client.cookies.set("esg_session", tampered)
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_expired_session(client, monkeypatch):
    import time
    from app.auth.service import make_session
    from app.core.config import settings

    token = make_session("admin")
    monkeypatch.setattr(settings, "session_ttl_hours", 0)
    time.sleep(1)
    client.cookies.set("esg_session", token)
    assert client.get("/api/auth/me").status_code == 401


def test_login_sets_expected_cookie_attributes(client, db):
    limiter._hits.clear()
    db.admin_users.insert_one({"username": "admin", "password": hash_password("admin123")})
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    lower = set_cookie.lower()
    assert "esg_session=" in lower
    assert "httponly" in lower
    assert "samesite=lax" in lower
    assert "max-age=28800" in lower


def test_unknown_username_runs_dummy_bcrypt_check(monkeypatch, db):
    import app.auth.service as service_mod

    calls = []
    real_checkpw = service_mod.bcrypt.checkpw

    def spy_checkpw(password, hashed):
        calls.append((password, hashed))
        return real_checkpw(password, hashed)

    monkeypatch.setattr(service_mod.bcrypt, "checkpw", spy_checkpw)

    result = authenticate("no-such-user", "whatever")

    assert result is False
    assert len(calls) == 1
    assert calls[0][1] == service_mod._DUMMY_HASH
