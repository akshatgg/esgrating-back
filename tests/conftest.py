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


@pytest.fixture(autouse=True)
def openai_provider(monkeypatch):
    """Tests run against the OpenAI provider whatever the machine's AWS credentials are.

    The shipped default is AWS (app/core/llm_settings.py), and resolve() would return it on
    any developer machine with a profile -- which would send every test through the Bedrock
    model id and make the suite depend on whose laptop it runs on. A test that wants AWS
    asks for it."""
    from app.core import bedrock, llm_settings
    monkeypatch.setattr(llm_settings, "resolve", lambda: llm_settings.OPENAI)
    # No test talks to Bedrock. Listing models is ~50 AWS calls; a test that wants a list
    # provides its own.
    monkeypatch.setattr(bedrock, "usable_models", lambda refresh=False: [])


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

    Step 0 (classification) answers in the client's CLASSIFY_GUIDE shape, with
    `primary_pillars` -- every pillar unless a test passes its own list. Classification no
    longer decides what is scored (his sections 3 and 18), so this only sets what the page
    record reports.
    """
    def __init__(self, scores, categories=("Environment", "Social", "Governance")):
        self.scores, self.calls = scores, []
        self.categories = list(categories)

    def generate_score(self, text):
        self.calls.append(text)
        if "STRICTLY SELECT THE TOP 5 KEYWORDS" in text:
            return json.dumps({"keywords": ["k1", "k2", "k3", "k4", "k5"]})
        if "CFC ESG Page Classification" in text:
            return json.dumps({"primary_pillars": self.categories, "secondary_pillars": [],
                               "relevance": "Substantive ESG", "themes": [],
                               "classification_reason": "fake"})
        for cat, score in self.scores.items():
            if f"[{cat}]" in text:
                return json.dumps({"reason": "r", "score": score, "positive_keywords": ["k1", "k2"],
                                   "negative_keywords": ["n1"], "sector": "finance", "industry": "banking"})
        return "An unexpected error occurred: boom"


@pytest.fixture
def prompts(db):
    for cat in ("Environment", "Social", "Governance"):
        db.esg_prompts.insert_one({"category": cat, "prompt": f"[{cat}] score this: {{text}}"})
