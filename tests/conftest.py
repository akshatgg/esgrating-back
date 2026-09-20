import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.core import db as dbmod
from app.core.config import settings

# app.main's lifespan refuses to start without a real (32+ char) SESSION_SECRET; don't
# depend on a local .env being present.
settings.session_secret = "test-session-secret-" + "x" * 44


@pytest.fixture(autouse=True)
def no_openai_keys(monkeypatch):
    """No test ever reaches the real OpenAI API. Every AI call in the app is faked by the
    test that needs it, but `settings` reads the developer's own .env, so a path nobody
    thought to patch would quietly dial out with a real key (and hang on retries). With
    the keys blank, code that checks for one skips; code that does not still meets a fake.
    A test that wants the check to pass sets its own key."""
    monkeypatch.setattr(settings, "esg_openai_api_key", "")
    monkeypatch.setattr(settings, "bfsi_openai_api_key", "")


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
    """Scores by category keyword in the prompt; keyword selection returns first 5.

    Step 0 (classification) answers with `categories` -- every category unless a test
    passes its own list, so a page is scored for exactly those categories.
    """
    def __init__(self, scores, categories=("Environment", "Social", "Governance")):
        self.scores, self.calls = scores, []
        self.categories = list(categories)

    def generate_score(self, text):
        self.calls.append(text)
        if "STRICTLY SELECT THE TOP 5 KEYWORDS" in text:
            return json.dumps({"keywords": ["k1", "k2", "k3", "k4", "k5"]})
        if '"categories"' in text:
            return json.dumps({"categories": self.categories})
        for cat, score in self.scores.items():
            if f"[{cat}]" in text:
                return json.dumps({"reason": "r", "score": score, "positive_keywords": ["k1", "k2"],
                                   "negative_keywords": ["n1"], "sector": "finance", "industry": "banking"})
        return "An unexpected error occurred: boom"


@pytest.fixture
def prompts(db):
    for cat in ("Environment", "Social", "Governance"):
        db.esg_prompts.insert_one({"category": cat, "prompt": f"[{cat}] score this: {{text}}"})
