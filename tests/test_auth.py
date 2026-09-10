import bcrypt
from app.auth.service import hash_password, verify_password, limiter


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
