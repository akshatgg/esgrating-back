import io, json
import docx
import pytest
from bson import ObjectId

from app.esg import llm as llm_mod
from app.esg import pipeline, store
from app.esg.extract import process_files
from tests.fixtures.make_pdf import make_pdf


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


def test_evaluate_score_truncates():
    assert pipeline.evaluate_score(90.4) == ("A", "Excellent")
    assert pipeline.evaluate_score(90.99) == ("A", "Excellent")
    assert pipeline.evaluate_score(91) == ("A+", "Outstanding")
    assert pipeline.evaluate_score(79.9) == ("B+", "Very Good")
    assert pipeline.evaluate_score(70.9) == ("B", "Good")
    assert pipeline.evaluate_score(60.5) == ("C", "Average")
    assert pipeline.evaluate_score(39.99) == ("D", "Below Average")


def test_extract_pdf_and_docx():
    pages = process_files([("r.pdf", make_pdf(["alpha page", "beta page"]))])
    assert [p["page_no"] for p in pages] == [1, 2] and "alpha" in pages[0]["text"]
    d = docx.Document(); d.add_paragraph("one"); d.add_paragraph("two")
    buf = io.BytesIO(); d.save(buf)
    assert process_files([("r.docx", buf.getvalue())]) == [{"page_no": 1, "text": "onetwo"}]


def test_full_run_scores_composite_and_persists(db, prompts, monkeypatch):
    fake = FakeLLM({"Environment": 80, "Social": 60, "Governance": 70})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    cid = store.insert_user("Asha", "asha@x.com", "Acme", "9876543210", ["r.pdf"])
    final = pipeline.calculate_esg_score_concurrent([("r.pdf", make_pdf(["p1 text", "p2 text"]))], cid, "2024-2025")
    assert final["environmental_score"] == 80 and final["social_score"] == 60 and final["governance_score"] == 70
    assert final["composite_score"] == pytest.approx(0.3 * 80 + 0.3 * 60 + 0.4 * 70)
    # Brief expected "B+"; the original evaluate_score (helper.py:99-112) maps int(70.0)=70 to
    # the 61..70 band -> ("B", "Good"). The source wins.
    assert final["composite_score_performance"] == "B"
    assert final["sector"] == "Finance" and final["industry"] == "Banking"
    assert final["environmental_top_keywords"] == ["k1", "k2", "k3", "k4", "k5"]
    scoring_calls = [c for c in fake.calls if "score this" in c]
    assert len(scoring_calls) == 6  # 2 pages x 3 categories
    assert db.esg_report.count_documents({"company_id": cid}) == 1
    assert db.esg_hashes.count_documents({"company_id": cid}) == 1


def test_cache_hit_returns_early_without_new_rows(db, prompts, monkeypatch):
    fake = FakeLLM({"Environment": 50, "Social": 50, "Governance": 50})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    pdf = make_pdf(["same text"])
    c1 = store.insert_user("A", "a@x.com", "A", "9876543210", ["r.pdf"])
    pipeline.calculate_esg_score_concurrent([("r.pdf", pdf)], c1, "2024-2025")
    n_calls = len(fake.calls)
    c2 = store.insert_user("B", "b@x.com", "B", "9876543211", ["r.pdf"])
    again = pipeline.calculate_esg_score_concurrent([("r.pdf", pdf)], c2, "2024-2025")
    assert len(fake.calls) == n_calls and again["composite_score"] == pytest.approx(50)
    assert db.esg_report.count_documents({"company_id": c2}) == 0  # quirk kept on purpose


def test_get_esg_score_branches(db):
    cid = ObjectId()
    assert store.get_esg_score(cid)["status"] is False
    db.esg_report.insert_one({"company_id": cid, "report_year": "2023-2024", "composite_score": 60.0})
    one = store.get_esg_score(cid)
    assert one["previous_year"] == "N/A" and one["trend_flag"] == "positive"
    db.esg_report.insert_one({"company_id": cid, "report_year": "2024-2025", "composite_score": 55.0})
    two = store.get_esg_score(cid)
    assert two["latest_year"] == "2024-2025" and two["difference"] == pytest.approx(-5.0) and two["trend_flag"] == "negative"


def test_insert_user_upserts_by_email(db):
    a = store.insert_user("A", "same@x.com", "Acme", "1", ["f1.pdf"])
    b = store.insert_user("A2", "same@x.com", "Acme", "1", ["f2.pdf", "f1.pdf"])
    assert a == b
    assert db.user_details.find_one({"_id": a})["file_names"] == ["f1.pdf", "f2.pdf"]


def test_missing_prompt_fails_loudly(db):
    with pytest.raises(RuntimeError, match="ESG prompt for Environment is missing"):
        store.read_prompt("Environment")


# --- additions beyond the brief -------------------------------------------------------

def test_missing_prompt_aborts_run_without_scoring_or_rows(db, monkeypatch):
    db.esg_prompts.insert_one({"category": "Environment", "prompt": "[Environment] score this: {text}"})
    fake = FakeLLM({"Environment": 80})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    cid = store.insert_user("A", "a@x.com", "A", "1", ["r.pdf"])
    out = pipeline.calculate_esg_score_concurrent([("r.pdf", make_pdf(["t"]))], cid, "2024-2025")
    assert "ESG prompt for Social is missing in esg_prompts" in out["error"]
    assert fake.calls == []
    assert db.esg_report.count_documents({}) == 0 and db.esg_hashes.count_documents({}) == 0


def test_aggregate_skips_unparseable_results(db, monkeypatch):
    fake = FakeLLM({})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    good = json.dumps({"score": 81, "sector": "energy", "industry": "power", "positive_keywords": ["a"]})
    other = json.dumps({"score": 70, "sector": "energy", "industry": "coal", "positive_keywords": ["b"]})
    avg, sector, industry, kws = pipeline.aggregate_scores(
        ["An unexpected error occurred: boom", good, other], "Environment")
    assert avg == 75.5 and sector == "energy" and industry == "power"
    assert kws == ["k1", "k2", "k3", "k4", "k5"]
    assert "['a', 'b']" in fake.calls[-1]  # keywords_list is repr(list) in the f-string


def test_no_text_returns_status_error(db):
    out = pipeline.calculate_esg_score_concurrent([("r.txt", b"hello")], ObjectId(), "2024-2025")
    assert out == {"status": "error", "message": "No text extracted from the input file or URL."}


class _Completions:
    def __init__(self, fail=False):
        self.kwargs, self.fail = None, fail

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.fail:
            raise ValueError("nope")
        msg = type("M", (), {"content": '{"score": 1}'})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _client(completions):
    return type("Cl", (), {"chat": type("Ch", (), {"completions": completions})})


def test_generate_score_params_and_error_string():
    comp = _Completions()
    m = llm_mod.GPTModel("k", "gpt-x", client=_client(comp))
    assert m.generate_score("hi") == '{"score": 1}'
    assert comp.kwargs == {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}],
                           "temperature": 0.01, "presence_penalty": 0.5, "seed": 123,
                           "response_format": {"type": "json_object"}}
    bad = llm_mod.GPTModel("k", "gpt-x", client=_client(_Completions(fail=True)))
    assert bad.generate_score("hi") == "An unexpected error occurred: nope"
