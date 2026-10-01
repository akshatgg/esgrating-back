"""The company's stock-exchange sector (app/esg/sector.py).

The client's prompt allows exactly two answers -- a sector name, or "Not Verified" -- and the
sector is the materiality key for the methodology's Annexure A weighting, so these tests are
mostly about what is refused. No network and no API key: every call goes through a fake.
"""
from datetime import date

import pytest

from app.core import llm_settings
from app.esg import sector


class _Response:
    def __init__(self, text):
        self.output_text = text


class FakeClient:
    """Stands in for OpenAI(): the Responses API with a web search tool."""

    def __init__(self, *answers, fail_on=()):
        self.answers, self.fail_on, self.calls = list(answers), fail_on, []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["tools"][0]["type"] in self.fail_on:
            raise RuntimeError("tool not supported")
        return _Response(self.answers.pop(0) if self.answers else "")


class FakeModel:
    """Stands in for the Bedrock scoring model: plain text in, plain text out."""

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def generate_score(self, text):
        self.prompts.append(text)
        return self.answer


@pytest.mark.parametrize("answer, expected", [
    ("Materials", "Materials"),
    ('"Capital Goods"', "Capital Goods"),
    ("  Food, Beverage & Tobacco.\n", "Food, Beverage & Tobacco"),
])
def test_a_sector_name_is_read_as_given(answer, expected):
    assert sector.sector_name(answer) == expected


@pytest.mark.parametrize("answer", [
    "Not Verified", '"Not Verified"', "not verified.", "", None,
    "An unexpected error occurred: AccessDeniedException",
    "The company is listed on the CSE.\nIts sector is Materials.",
    "x" * 200,
])
def test_anything_that_is_not_a_sector_name_is_refused(answer):
    """An error string or a paragraph of reasoning must never be printed on a rating report
    as though it were a sector."""
    assert sector.sector_name(answer) is None


def test_the_prompt_sent_is_the_clients_word_for_word():
    client = FakeClient("Materials")
    sector.search("Alumex PLC", client=client)
    sent = client.calls[0]["input"]
    assert sent[0]["content"] == sector.SECTOR_SYSTEM
    assert "OUTPUT ONLY THE SECTOR NAME." in sector.SECTOR_SYSTEM
    assert "Sri Lankan company" in sector.SECTOR_SYSTEM
    assert sent[1]["content"] == "Company name: Alumex PLC"


def test_a_web_lookup_is_stored_as_verified_today():
    listing = sector.search("Alumex PLC", client=FakeClient("Materials"))
    assert listing["sector"] == "Materials"
    assert listing["source"] == sector.WEB_SOURCE
    assert listing["verification_date"] == date.today().isoformat()
    assert listing["stock_exchange"] == "" and listing["ticker"] == ""
    assert set(listing) == set(sector.FIELDS)


def test_not_verified_from_the_web_stores_nothing():
    assert sector.search("Nobody Ltd", client=FakeClient("Not Verified")) is None


def test_the_older_tool_name_is_tried_when_the_current_one_is_refused():
    client = FakeClient("Materials", fail_on=("web_search",))
    assert sector.search("Alumex PLC", client=client)["sector"] == "Materials"
    assert [c["tools"][0]["type"] for c in client.calls] == list(sector.TOOL_TYPES)


def test_no_company_name_makes_no_call():
    client = FakeClient("Materials")
    assert sector.search("   ", client=client) is None and client.calls == []


def test_bedrock_cannot_run_the_web_lookup(monkeypatch):
    monkeypatch.setattr(llm_settings, "resolve", lambda: llm_settings.AWS)
    assert sector.available() is False and sector.search("Alumex PLC") is None


def test_knowledge_gives_the_sector_and_stores_it_as_unverified():
    """Accepted on AWS, where nothing can search (user, 2026-10-01). The record must never
    pass for one checked against the exchange: fixed source text and no date."""
    model = FakeModel("Materials")
    listing = sector.from_knowledge("Alumex PLC", llm=model)
    assert listing["sector"] == "Materials"
    assert listing["source"] == sector.UNVERIFIED_SOURCE and listing["verification_date"] == ""
    assert sector.SECTOR_SYSTEM in model.prompts[0] and "Alumex PLC" in model.prompts[0]


@pytest.mark.parametrize("answer", ["Not Verified", "An unexpected error occurred: throttled", ""])
def test_knowledge_with_no_sector_leaves_the_field_for_the_analyst(answer):
    assert sector.from_knowledge("Nobody Ltd", llm=FakeModel(answer)) is None


def test_the_openai_scoring_client_is_asked_for_plain_text():
    """That client forces JSON output for scoring; this prompt answers in plain text, so it
    is called directly without the JSON mode."""
    class Chat:
        def __init__(self):
            self.kwargs = None
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            self.kwargs = kwargs
            message = type("M", (), {"content": "Banks"})()
            return type("R", (), {"choices": [type("C", (), {"message": message})()]})()

    class Gpt:
        model = "gpt-4.1-mini"
        clients = Chat()

    assert sector.from_knowledge("Seylan Bank PLC", llm=Gpt())["sector"] == "Banks"
    assert "response_format" not in Gpt.clients.kwargs


def test_lookup_prefers_the_web_and_falls_back_to_knowledge(monkeypatch):
    web, known = {"sector": "Capital Goods"}, {"sector": "Materials"}
    monkeypatch.setattr(sector, "from_knowledge", lambda name: known)
    monkeypatch.setattr(sector, "search", lambda name: web)
    assert sector.lookup("Acme PLC") is web
    monkeypatch.setattr(sector, "search", lambda name: None)   # AWS: nothing can search
    assert sector.lookup("Acme PLC") is known
