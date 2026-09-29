"""The official stock-exchange sector (app/esg/sector.py).

The client's rule is that the sector must be read off an official exchange source, never
supplied from what a model already knows -- and the sector is also the materiality key for
the methodology's Annexure A weighting, so a confident wrong answer silently reweights the
whole rating. These tests are about what the module REFUSES to store.

No network and no API key is touched: every call goes through a fake client.
"""
from datetime import date

import pytest

from app.core import llm_settings
from app.esg import sector


GOOD = ('{"company_name": "Hayleys PLC", "stock_exchange": "Colombo Stock Exchange", '
        '"ticker": "HAYL.N0000", "sector": "Capital Goods", '
        '"source": "https://cse.lk/pages/company-profile/HAYL.N0000", '
        '"verification_date": "2020-01-01"}')


class _Response:
    def __init__(self, text):
        self.output_text = text


class FakeClient:
    """Stands in for OpenAI(). Records the calls so the prompt can be asserted on."""

    def __init__(self, *answers, fail_on=()):
        self.answers = list(answers)
        self.fail_on = fail_on
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool = kwargs["tools"][0]["type"]
        if tool in self.fail_on:
            raise RuntimeError(f"{tool} is not supported")
        return _Response(self.answers.pop(0) if self.answers else "")


def test_a_verified_listing_is_returned_whole():
    listing = sector.lookup("Hayleys PLC", client=FakeClient(GOOD))
    assert listing["stock_exchange"] == "Colombo Stock Exchange"
    assert listing["ticker"] == "HAYL.N0000"
    assert listing["sector"] == "Capital Goods"
    assert listing["source"].startswith("https://cse.lk/")
    assert set(listing) == set(sector.FIELDS)


def test_the_verification_date_is_ours_not_the_models():
    """A verification date records when WE checked. It is the one field a model has no
    standing to report, and the answer above deliberately backdates it."""
    listing = sector.lookup("Hayleys PLC", client=FakeClient(GOOD))
    assert listing["verification_date"] == date.today().isoformat()


def test_an_answer_with_no_source_is_discarded():
    """A sector with no source was not read off an exchange -- it was recalled, which is
    the one thing the client's brief forbids."""
    answer = '{"company_name": "Hayleys PLC", "sector": "Capital Goods", "source": ""}'
    assert sector.lookup("Hayleys PLC", client=FakeClient(answer)) is None


def test_an_answer_with_no_sector_is_discarded():
    answer = '{"company_name": "Hayleys PLC", "sector": "", "source": "https://cse.lk/x"}'
    assert sector.lookup("Hayleys PLC", client=FakeClient(answer)) is None


def test_an_unlisted_company_yields_nothing_rather_than_a_guess():
    """Blank is a correct answer: the report then shows a blank an analyst can fill, which
    is a smaller error than a sector nobody checked."""
    blank = '{"company_name": "", "stock_exchange": "", "ticker": "", "sector": "", "source": ""}'
    assert sector.lookup("Some Private Ltd", client=FakeClient(blank)) is None


@pytest.mark.parametrize("wrapped", [
    f"```json\n{GOOD}\n```",
    f"Here is what I found.\n\n{GOOD}\n\nSources: cse.lk",
])
def test_json_is_read_out_of_a_fenced_or_narrated_answer(wrapped):
    """A search answer often arrives with citations or a fence around it."""
    assert sector.lookup("Hayleys PLC", client=FakeClient(wrapped))["sector"] == "Capital Goods"


def test_an_unparseable_answer_is_not_stored():
    assert sector.lookup("Hayleys PLC", client=FakeClient("I could not determine this.")) is None


def test_the_older_tool_name_is_tried_when_the_current_one_is_refused():
    """Accounts served the older API surface call it web_search_preview. The lookup must not
    go dark on them."""
    client = FakeClient(GOOD, fail_on=("web_search",))
    assert sector.lookup("Hayleys PLC", client=client)["sector"] == "Capital Goods"
    assert [c["tools"][0]["type"] for c in client.calls] == list(sector.TOOL_TYPES)


def test_a_refused_answer_is_not_retried_with_the_other_tool():
    """Only a failed CALL falls through to the older name. An answer that came back and was
    rejected is a real 'not found', and searching again would just spend a second call."""
    client = FakeClient('{"sector": "", "source": ""}', GOOD)
    assert sector.lookup("Hayleys PLC", client=client) is None
    assert len(client.calls) == 1


def test_no_company_name_makes_no_call():
    client = FakeClient(GOOD)
    assert sector.lookup("   ", client=client) is None
    assert client.calls == []


def test_the_prompt_sent_is_the_one_on_disk():
    client = FakeClient(GOOD)
    sector.lookup("Hayleys PLC", client=client)
    sent = client.calls[0]["input"]
    assert sent[0]["content"] == sector.SECTOR_SYSTEM
    assert "Hayleys PLC" in sent[1]["content"]


def test_bedrock_cannot_run_the_lookup(monkeypatch):
    """Converse has no web search, so on AWS the field is the analyst's to fill. Said here
    rather than discovered as a failed call on every report."""
    monkeypatch.setattr(llm_settings, "resolve", lambda: llm_settings.AWS)
    assert sector.available() is False
    assert sector.lookup("Hayleys PLC") is None


def test_no_key_means_no_lookup(monkeypatch):
    monkeypatch.setattr(llm_settings, "resolve", lambda: llm_settings.OPENAI)
    monkeypatch.setattr(sector.settings, "esg_openai_api_key", "")
    assert sector.available() is False
