import json

import httpx
import pytest

from app.bfsi import pipeline
from app.bfsi.openai_client import BfsiOpenAiFatal, OpenAiJson

UNIT = {
    "reason": "because",
    "score": 60,
    # KPI scores (0-100): point 1 at 90, point 2 at 30 -> page score 60.
    "kpi_scores": [[1, 90], [2, 30]],
    "positive_keywords": ["solar", "waste"],
    "negative_keywords": ["fine", "spill"],
    "sector": "finance",
    "industry": "banking",
}

QUAL = {
    "top_risks": ["r1"],
    "top_improvements": ["i1"],
    "climate_risk": "warm",
    "governance_summary": "ok",
    "key_metrics": {"employees": "10"},
}


class FakeClient:
    """Stand-in for OpenAiJson: records every prompt, returns canned JSON."""

    def __init__(self, responder=None, json_responder=None,
                 categories=("Environment", "Social", "Governance")):
        self.batches = []
        self.json_calls = []
        self._responder = responder or (lambda key, prompt: dict(UNIT))
        self._json_responder = json_responder or _default_json
        self.categories = list(categories)   # step 0: the categories every unit is about

    def batch(self, prompts, concurrency=8):
        self.batches.append(dict(prompts))
        if prompts and all('"categories"' in p for p in prompts.values()):
            return {k: {"categories": list(self.categories)} for k in prompts}
        return {k: self._responder(k, p) for k, p in prompts.items()}

    @property
    def scoring_batches(self):
        """The three category batches, without the step 0 classification batch."""
        return [b for b in self.batches
                if not (b and all('"categories"' in p for p in b.values()))]

    def json(self, user, system=""):
        self.json_calls.append((user, system))
        return self._json_responder(user, system)

    # convenience for assertions
    @property
    def unit_prompts(self):
        return [p for b in self.scoring_batches for p in b.values()]

    @property
    def keyword_prompts(self):
        return [u for u, _s in self.json_calls if "STRICTLY SELECT THE TOP 5 KEYWORDS" in u]

    @property
    def qual_calls(self):
        return [(u, s) for u, s in self.json_calls if "STRICTLY SELECT THE TOP 5 KEYWORDS" not in u]


def _default_json(user, system=""):
    if "STRICTLY SELECT THE TOP 5 KEYWORDS" in user:
        return {"keywords": ["p1", "p2", "p3", "p4", "p5"]}
    return dict(QUAL)


@pytest.fixture(autouse=True)
def _no_prompts_in_db(db):
    """An empty database: the scoring prompts use the built-in CRITERIA (18/18/17 KPIs)."""
    return db


@pytest.fixture
def fake(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(pipeline, "get_client", lambda: c)
    return c


def use(monkeypatch, client):
    monkeypatch.setattr(pipeline, "get_client", lambda: client)
    return client


SUBMISSION = {"borrower_name": "Acme Ltd", "industry": "manufacturing", "loan_type": "Term Loan"}
PAGES = [{"page_no": 1, "text": "alpha beta"}, {"page_no": 2, "text": "gamma delta"}]


# --------------------------------------------------------------------------- prompts

def test_criteria_are_verbatim():
    # Full equality against the literal, not substrings — a substring/line-count
    # check would miss a change like "Sanitation Practises" -> "Practices" or a
    # reordered/re-indented line as long as the start/end anchors still matched.
    assert pipeline.CRITERIA["E"] == """1. Renewable energy usage initiatives.
2. Carbon footprint reduction goals.
3. Waste management policies.
4. Transparency in environmental disclosures.
5. Biodiversity conservation efforts.
6. Water conservation and management practices.
7. Pollution control measures.
8. Sustainable sourcing and supply chain practices.
9. Compliance with environmental regulations.
10. Any other relevant environmental factors.
11. Urbanisation and Economic Growth
12. Climate and Resilience
13. Sanitation Practises
14. Housing and Urban Infrastructure
15. Affordable and clean energy
16. Climate action
17. Life below the river
18. Life on land"""
    assert pipeline.CRITERIA["S"] == """1. Diversity, equity, and inclusion efforts.
2. Employee welfare and safety.
3. Community engagement initiatives.
4. Human rights policies and practices.
5. Data and Digital
6. Youth and Inclusion
7. Urban Livelihood
8. No poverty
9. Zero hunger
10. Good health and well - being
11. Quality education
12. Gender Equality
13. Access to Clean water and sanitation
14. Decent work and economic growth
15. Sustainable cities and communities
16. Reduced inequality
17. Responsible consumption and production
18. Peace, justice, and strong institution"""
    assert pipeline.CRITERIA["G"] == """1. Board diversity and ethical leadership.
2. Compliance with regulations.
3. Accountability and stakeholder involvement.
4. Anti-corruption and bribery policies.
5. Executive compensation practices.
6. Shareholder rights and activism.
7. Data privacy and security measures.
8. Risk management and internal controls.
9. Transparency in financial reporting.
10. Legal and regulatory compliance.
11. Business ethics and code of conduct.
12. Corporate social responsibility initiatives.
13. Whistleblower protection policies.
14. Conflicts of interest management.
15. Urban Governance and Municipal Finance
16. Industry, innovation, and infrastructure
17. Partnership for the goals."""


def test_keyword_prompt_keeps_typo_missing_number_and_indents():
    # Full equality, not substrings/line-index checks — those could pass while
    # unrelated lines drifted; this pins the whole verbatim-ported string.
    assert pipeline.KEYWORD_PROMPT == """you are AI Assitant, you have great expert in ESG, you can select the best keyword from the list of keywords
    Before selecting the top 5 keyword from the given list of keywords, you need to check the following:
    1. Understand the category of the keywords
    2. Understand the context of the keywords
    3. Understand the relevance of the keywords
    5. Understand the importance of the keywords
    6. STRICTLY SELECT THE TOP 5 KEYWORDS which will be more relevant to the category of the keywords.
    %1$s

    %2$s
    Provide the response in JSON format with the following fields:
    - "keywords": A list of keywords or phrase maximum 5."""


def test_qual_system_message_verbatim():
    assert pipeline.QUAL_SYSTEM == (
        'You are an ESG credit-risk analyst. Using the report text below, respond in JSON: '
        '{"top_risks":[<5 short strings>], "top_improvements":[<5 short strings>], '
        '"climate_risk":"<2-3 sentences>", "governance_summary":"<2-3 sentences>", '
        '"key_metrics":{"employees":"","women_pct":"","attrition":"","complaints":"","csr":""}}'
    )


def test_category_prompt_renders_adjective_criteria_and_text(fake):
    # Full equality on the raw template, not substrings, so a drift in the
    # unrendered %N$s placeholders or surrounding text can't slip through.
    assert pipeline.CATEGORY_PROMPT == """Analyze the following text for %1$s performance. Evaluate the text based on:
%2$s

Identify the positive and negative keywords that influenced the score.

Provide the response in JSON format with the following fields:
- "reason": A Detailed explanation of the score.
- "score": A number between 0 and 100.
- "positive_keywords": A list of keywords or phrases that contributed positively to the score.
- "negative_keywords": A list of keywords or phrases that reduced the score.
- "sector": The sector of the company (e.g., technology, healthcare, finance).
- "industry": The industry of the company (e.g., software, pharmaceuticals, banking).

Text:
%3$s"""
    pipeline.bfsi_analyze(SUBMISSION, PAGES)
    first = fake.scoring_batches[0][0]
    assert first.startswith("Analyze the following text for environmental performance. Evaluate the text based on:\n")
    assert pipeline.CRITERIA["E"] in first
    assert first.endswith("\nText:\nalpha beta")
    # The leftover page-score fields are dropped from the prompt as it is sent: the KPI
    # scores are the marks, and "reason" and the keyword lists now explain those marks.
    assert '- "score": A number between 0 and 100.' not in first
    assert '- "reason": A Detailed explanation of the score.' not in first
    assert '- "kpi_scores": A list of [point number, score] pairs' in first and "81-100:" in first
    assert '- "reason": One line for each point you scored' in first
    assert '- "positive_keywords": The words or short phrases from this text that earned' in first
    assert '- "negative_keywords": The words or short phrases from this text that show poor' in first
    assert "%1$s" not in first and "%2$s" not in first and "%3$s" not in first
    assert fake.scoring_batches[1][0].startswith("Analyze the following text for social performance.")
    assert fake.scoring_batches[2][0].startswith("Analyze the following text for governance performance.")


def test_only_the_categories_a_unit_is_about_are_scored(monkeypatch):
    """Step 0, the same as the ESG calculator: a unit is classified first and only that
    category's KPIs are matched against it."""
    c = use(monkeypatch, FakeClient(categories=["Environment"]))
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert len(c.scoring_batches) == 1
    assert all("environmental performance" in p for p in c.scoring_batches[0].values())
    # UNIT scores point 1 = 90 and point 2 = 30 of the 18 built-in E criteria: 120 / 1800.
    assert out["e_score"] == 6.67 and out["s_score"] == 0.0 and out["g_score"] == 0.0
    assert out["scoring_method"] == "kpi_score"


# --------------------------------------------------------------------------- units / helpers

def test_page_units_splits_long_pages_and_drops_empty():
    pages = [{"page_no": 1, "text": " ".join(["w"] * 2500)}, {"page_no": 2, "text": "   "},
             {"page_no": 3, "text": "one two"}]
    units = pipeline.page_units(pages)
    assert [u["page_no"] for u in units] == [1, 1, 1, 3]        # array_chunk: 1200/1200/100
    assert [len(u["text"].split(" ")) for u in units] == [1200, 1200, 100, 2]
    assert units[3]["text"] == "one two"


def test_avg_scores_clamps_each_score_then_averages():
    assert pipeline.avg_scores([{"score": 150}, {"score": 50}]) == 75.0     # 150 clamped to 100
    assert pipeline.avg_scores([{"score": -5}, {"score": 50}]) == 25.0      # -5 clamped to 0
    assert pipeline.avg_scores([{"score": "80"}, {"score": None}, {}]) == 80.0
    assert pipeline.avg_scores([]) == 0.0
    assert pipeline.avg_scores([{"score": "abc"}]) == 0.0
    assert pipeline.avg_scores([{"score": 1}, {"score": 2}, {"score": 2}]) == 1.67


def test_pool_dedupes_first_seen_and_caps():
    results = [{"k": ["b", "a", " b "]}, {"k": ["c", "", "d"]}, {"k": ["e", "f"]}]
    assert pipeline.pool(results, "k", 5) == ["b", "a", "c", "d", "e"]
    assert pipeline.pool(results, "missing", 5) == []
    # PHP (array) cast: a scalar or an object still yields keywords
    assert pipeline.pool([{"k": "solo"}, {"k": {"x": "obj"}}, {"k": None}], "k", 5) == ["solo", "obj"]


def test_modal_is_ucfirst_lowercased_most_common():
    results = [{"s": "FINANCE"}, {"s": "finance"}, {"s": " technology "}, {"s": ""}]
    assert pipeline.modal(results, "s") == "Finance"
    assert pipeline.modal([], "s") == ""


# --------------------------------------------------------------------------- select_keywords

def test_select_keywords_skips_llm_for_five_or_fewer(fake):
    assert pipeline.select_keywords("Environment", ["a", "b", "c"]) == ["a", "b", "c"]
    assert fake.json_calls == []


def test_select_keywords_ranks_via_llm_and_takes_first_five(fake):
    picked = pipeline.select_keywords("Environment", ["a", "b", "c", "d", "e", "f"])
    assert picked == ["p1", "p2", "p3", "p4", "p5"]
    user, system = fake.json_calls[0]
    assert system == ""                                # user message only, like the ESG calculator
    assert "\n    Environment\n" in user
    assert '["a","b","c","d","e","f"]' in user         # PHP json_encode: no spaces


def test_select_keywords_falls_back_to_first_five_when_ranking_returns_none(monkeypatch):
    use(monkeypatch, FakeClient(json_responder=lambda u, s="": None))
    assert pipeline.select_keywords("Social", ["a", "b", "c", "d", "e", "f"]) == ["a", "b", "c", "d", "e"]


def test_select_keywords_falls_back_when_ranking_raises(monkeypatch):
    def boom(u, s=""):
        raise RuntimeError("OpenAI call failed after retries (HTTP 500)")
    use(monkeypatch, FakeClient(json_responder=boom))
    assert pipeline.select_keywords("Governance", ["a", "b", "c", "d", "e", "f"]) == ["a", "b", "c", "d", "e"]


def test_select_keywords_reraises_fatal(monkeypatch):
    def boom(u, s=""):
        raise BfsiOpenAiFatal("OpenAI API key is invalid.")
    use(monkeypatch, FakeClient(json_responder=boom))
    with pytest.raises(BfsiOpenAiFatal):
        pipeline.select_keywords("Governance", ["a", "b", "c", "d", "e", "f"])


# --------------------------------------------------------------------------- bfsi_analyze

def test_two_pages_produce_six_category_calls(fake):
    pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert len(fake.scoring_batches) == 3   # one per category; step 0 classification is separate
    assert len(fake.unit_prompts) == 6


def test_analyze_shape_scores_keywords_and_reasons(fake):
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    # Category = best KPI scores as a % of the maximum: E and S have 18 KPIs, G has 17.
    assert out["e_score"] == out["s_score"] == round((90 + 30) / 1800 * 100, 2)   # 6.67
    assert out["g_score"] == round((90 + 30) / 1700 * 100, 2)                      # 7.06
    assert out["scoring_method"] == "kpi_score"
    rows = {r["kpi"]: (r["score"], r["pages"]) for r in out["kpi_coverage"]["Environment"]["kpis"]}
    assert rows["Renewable energy usage initiatives."] == (90.0, [1, 2])
    assert rows["Carbon footprint reduction goals."] == (30.0, [1, 2])
    assert rows["Life on land"] == (0.0, [])
    assert out["keywords"] == {"E": ["solar", "waste"], "S": ["solar", "waste"], "G": ["solar", "waste"]}
    assert out["negative_keywords"]["E"] == ["fine", "spill"]
    # The page score is the average of the page's KPI scores, not the AI's own number.
    assert out["reasons"]["E"] == [
        {"page": 1, "score": 60.0, "reason": "because"},
        {"page": 2, "score": 60.0, "reason": "because"},
    ]
    assert out["top_risks"] == ["r1"] and out["top_improvements"] == ["i1"]
    assert out["climate_risk"] == "warm" and out["governance_summary"] == "ok"
    assert out["key_metrics"] == {"employees": "10"}
    assert set(out) == {
        "e_score", "s_score", "g_score", "keywords", "negative_keywords", "reasons",
        "detected_sector", "detected_industry", "top_risks", "top_improvements",
        "climate_risk", "governance_summary", "key_metrics", "kpi_coverage", "scoring_method",
    }


def test_reason_entries_carry_page_and_kpi_page_score(monkeypatch):
    def responder(key, prompt):
        return {"reason": " spaced " if key == 0 else "", "score": "nope", "page_no": 99}
    use(monkeypatch, FakeClient(responder=responder))
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    # blank reason dropped; no KPI scores -> page score 0 (the AI's "nope" is ignored)
    assert out["reasons"]["E"] == [{"page": 1, "score": 0.0, "reason": "spaced"}]
    # a page that arrived without a page_no casts to 0, as PHP's (int)null does
    out = pipeline.bfsi_analyze(SUBMISSION, [{"text": "alpha"}])
    assert out["reasons"]["E"] == [{"page": 0, "score": 0.0, "reason": "spaced"}]


def test_failed_units_are_left_out(monkeypatch):
    def responder(key, prompt):
        return None if key == 0 else dict(UNIT, kpi_scores=[[1, 80]])
    use(monkeypatch, FakeClient(responder=responder))
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert out["e_score"] == round(80 / 1800 * 100, 2)
    assert [r["page"] for r in out["reasons"]["E"]] == [2]


def test_negative_keywords_are_pooled_to_five_and_not_ranked(monkeypatch):
    negs = ["n1", "n2", "n3", "n4", "n5", "n6", "n7"]
    use(monkeypatch, FakeClient(responder=lambda k, p: dict(UNIT, negative_keywords=negs,
                                                            positive_keywords=["a"])))
    c = pipeline.get_client()
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert out["negative_keywords"]["E"] == ["n1", "n2", "n3", "n4", "n5"]
    assert c.keyword_prompts == []           # positives were <= 5, negatives never ranked


def test_positive_keywords_are_pooled_to_forty_before_ranking(monkeypatch):
    pos = [f"k{i}" for i in range(60)]
    c = use(monkeypatch, FakeClient(responder=lambda k, p: dict(UNIT, positive_keywords=pos)))
    pipeline.bfsi_analyze(SUBMISSION, PAGES)
    ranked = c.keyword_prompts[0]
    assert '"k39"' in ranked and '"k40"' not in ranked


def test_detected_sector_and_industry_are_modal_and_ucfirst(monkeypatch):
    def responder(key, prompt):
        return dict(UNIT, sector="ENERGY" if key == 0 else "energy", industry="banking")
    use(monkeypatch, FakeClient(responder=responder))
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert out["detected_sector"] == "Energy"
    assert out["detected_industry"] == "Banking"


def test_qualitative_call_gets_system_message_and_capped_digest(fake):
    pages = [{"page_no": 1, "text": " ".join(f"w{i}" for i in range(7000))}]
    pipeline.bfsi_analyze(SUBMISSION, pages)
    user, system = fake.qual_calls[0]
    assert system == pipeline.QUAL_SYSTEM
    head, digest = user.split("\n\nReport text (condensed):\n")
    assert head == "Company: Acme Ltd | Industry: manufacturing | Loan type: Term Loan"
    assert len(digest.split(" ")) == 6000
    assert digest.startswith("w0 w1 ") and digest.endswith(" w5999")


def test_missing_qual_fields_fall_back_to_defaults(monkeypatch):
    use(monkeypatch, FakeClient(json_responder=lambda u, s="": (
        {"keywords": ["p1"]} if "STRICTLY SELECT" in u else {})))
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    assert out["top_risks"] == [] and out["top_improvements"] == []
    assert out["climate_risk"] == "" and out["governance_summary"] == ""
    assert out["key_metrics"] == []


def test_fatal_from_scoring_aborts_the_analysis(monkeypatch):
    def responder(key, prompt):
        raise BfsiOpenAiFatal("OpenAI account has no available quota/credits — add billing at platform.openai.com and try again.")
    use(monkeypatch, FakeClient(responder=responder))
    with pytest.raises(BfsiOpenAiFatal, match="no available quota"):
        pipeline.bfsi_analyze(SUBMISSION, PAGES)


# --------------------------------------------------------------------------- OpenAiJson

def _ok_body(obj):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(obj)}}]})


def _make(handler):
    c = OpenAiJson("sk-test", "gpt-4o-mini", transport=httpx.MockTransport(handler))
    c._sleep = lambda s: sleeps.append(s)
    return c


sleeps = []


@pytest.fixture(autouse=True)
def _clear_sleeps():
    sleeps.clear()


def test_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["ct"] = request.headers["content-type"]
        seen["body"] = json.loads(request.content)
        return _ok_body({"ok": 1})

    assert _make(handler).json("hello", "sys") == {"ok": 1}
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["ct"] == "application/json"
    assert seen["body"] == {
        "model": "gpt-4o-mini", "temperature": 0.01, "seed": 123,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}],
    }
    assert "presence_penalty" not in seen["body"]


def test_empty_system_sends_user_message_only():
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return _ok_body({"ok": 1})

    _make(handler).json("hello")
    assert seen["messages"] == [{"role": "user", "content": "hello"}]


def test_401_is_fatal():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})

    with pytest.raises(BfsiOpenAiFatal, match="OpenAI API key is invalid."):
        _make(handler).json("x")
    assert len(calls) == 1          # no retries on a fatal


def test_403_is_fatal():
    with pytest.raises(BfsiOpenAiFatal, match=r"OpenAI rejected the request \(HTTP 403\)\."):
        _make(lambda r: httpx.Response(403, json={"error": {}})).json("x")


def test_insufficient_quota_is_fatal_on_any_status():
    body = {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}
    with pytest.raises(BfsiOpenAiFatal, match="no available quota/credits"):
        _make(lambda r: httpx.Response(429, json=body)).json("x")


def test_retries_twice_then_succeeds():
    codes = [500, 500, 200]

    def handler(request):
        code = codes.pop(0)
        return _ok_body({"score": 1}) if code == 200 else httpx.Response(code, text="boom")

    assert _make(handler).json("x") == {"score": 1}
    assert sleeps == [2, 4]


def test_raises_after_three_failed_tries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, text="boom")

    with pytest.raises(RuntimeError, match=r"OpenAI call failed after retries \(HTTP 500\)"):
        _make(handler).json("x")
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_non_dict_body_is_not_a_result():
    # bfsi_openai_parse() returns null for anything that is not a JSON object.
    assert OpenAiJson._parse(json.dumps({"choices": [{"message": {"content": "[1,2]"}}]})) is None
    assert OpenAiJson._parse(json.dumps({"choices": [{"message": {"content": "not json"}}]})) is None
    assert OpenAiJson._parse('{"no":"choices"}') is None
    assert OpenAiJson._parse("") is None
    # ... and such a 200 counts as a failed try, exactly like the PHP loop
    with pytest.raises(RuntimeError, match=r"\(HTTP 200\)"):
        _make(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "[1,2]"}}]})).json("x")


def test_missing_api_key_is_a_configuration_error():
    c = OpenAiJson("", "gpt-4o-mini")
    with pytest.raises(RuntimeError, match="OpenAI key not configured"):
        c.json("x")


def test_batch_preserves_keys_and_runs_in_waves():
    seen = []

    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        seen.append(prompt)
        return _ok_body({"score": int(prompt)})

    c = _make(handler)
    out = c.batch({f"k{i}": str(i) for i in range(20)}, concurrency=8)
    assert list(out) == [f"k{i}" for i in range(20)]
    assert out["k19"] == {"score": 19}
    assert len(seen) == 20


def test_batch_retries_a_failed_unit_three_times_then_yields_none():
    attempts = {"a": 0, "b": 0}

    def handler(request):
        key = json.loads(request.content)["messages"][0]["content"]
        attempts[key] += 1
        if key == "a":
            return _ok_body({"score": 1})
        return httpx.Response(500, text="boom")

    out = _make(handler).batch({"a": "a", "b": "b"})
    assert out == {"a": {"score": 1}, "b": None}
    assert attempts == {"a": 1, "b": 3}
    assert sleeps == [2, 2]        # one 2s pause per retry wave


def test_batch_aborts_everything_on_a_fatal():
    def handler(request):
        return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})

    with pytest.raises(BfsiOpenAiFatal, match="OpenAI API key is invalid."):
        _make(handler).batch({"a": "a", "b": "b"})


def test_get_client_is_cached_per_key_and_model(monkeypatch):
    from app.bfsi import openai_client as oc
    from app.core.config import settings
    monkeypatch.setattr(settings, "bfsi_openai_api_key", "sk-1")
    monkeypatch.setattr(settings, "bfsi_openai_model", "gpt-4o-mini")
    a = oc.get_client()
    assert oc.get_client() is a and a.api_key == "sk-1" and a.model == "gpt-4o-mini"
    monkeypatch.setattr(settings, "bfsi_openai_api_key", "sk-2")
    assert oc.get_client() is not a


def test_criteria_come_from_esg_kpis_with_sub_pillar_context(db, fake):
    for order, (sp, sp1, m) in enumerate([("Water", "Water I", "Water targets"), ("Water", "Water II", "Water withdrawn")], 1):
        for pillar in "ESG":
            db.esg_kpis.insert_one({"pillar": pillar, "sub_pillar": sp, "sub_pillar_1": sp1, "metric": m,
                                    "order": order, "is_meta": False})
    out = pipeline.bfsi_analyze(SUBMISSION, PAGES)
    first = fake.scoring_batches[0][0]
    assert "Sub Pillar: Water\n  Sub Pillar 1: Water I\n    1. Water targets\n  Sub Pillar 1: Water II\n    2. Water withdrawn" in first
    assert "Renewable energy usage initiatives." not in first
    # Same flow: UNIT scores point 1 = 90, point 2 = 30 -> (90 + 30) / 200 -> 60
    assert out["e_score"] == 60.0
    assert [k["kpi"] for k in out["kpi_coverage"]["Environment"]["kpis"]] == ["Water targets", "Water withdrawn"]
