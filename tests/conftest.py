import mongomock
import pytest
from fastapi.testclient import TestClient

from app.core import db as dbmod
from app.core.config import settings


@pytest.fixture
def db():
    database = mongomock.MongoClient()["esg_score_calculator"]
    dbmod.set_db(database)
    yield database
    dbmod.set_db(None)


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    return tmp_path


@pytest.fixture
def client(db, upload_dir):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client, db):
    from app.auth.service import hash_password, limiter
    limiter._hits.clear()
    db.admin_users.insert_one({"username": "admin", "password": hash_password("admin123")})
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    return client
