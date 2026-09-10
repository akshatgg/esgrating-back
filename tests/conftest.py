import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.core import db as dbmod
from app.core.config import settings

# app.main's lifespan refuses to start without a real (32+ char) SESSION_SECRET; don't
# depend on a local .env being present.
settings.session_secret = "test-session-secret-" + "x" * 44


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


class FakeLLM:
    """Scores by category keyword in the prompt; keyword selection returns first 5."""
    def __init__(self, scores):
        self.scores, self.calls = scores, []

    def generate_score(self, text):
        self.calls.append(text)
        if "STRICTLY SELECT THE TOP 5 KEYWORDS" in text:
            return json.dumps({"keywords": ["k1", "k2", "k3", "k4", "k5"]})
        for cat, score in self.scores.items():
            if f"[{cat}]" in text:
                return json.dumps({"reason": "r", "score": score, "positive_keywords": ["k1", "k2"],
                                   "negative_keywords": ["n1"], "sector": "finance", "industry": "banking"})
        return "An unexpected error occurred: boom"


@pytest.fixture
def prompts(db):
    for cat in ("Environment", "Social", "Governance"):
        db.esg_prompts.insert_one({"category": cat, "prompt": f"[{cat}] score this: {{text}}"})
