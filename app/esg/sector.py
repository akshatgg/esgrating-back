"""The company's official stock-exchange sector.

The prompt is the client's own (CFC, 2026-10-02; app/esg/prompts/stock_exchange_sector.txt)
and is sent word for word. It asks for one thing back -- the sector name, or "Not Verified"
-- so that is all this module reads: no exchange, no ticker, no source from the model.

Two ways of asking, the same prompt for both:

- search(): through the OpenAI Responses API with the web_search tool, which can actually
  open the exchange's page. Only possible with OpenAI as the provider.
- from_knowledge(): through whichever scoring model the dashboard has selected. Bedrock has
  no web search, so on AWS this is the model's own knowledge -- accepted there by decision
  (user, 2026-10-01) -- and the stored record says so rather than passing for a verified one.

"Not Verified", an error, or anything that is not a short sector name is treated as no
answer: the sector is left blank for an analyst to fill. It is also the materiality key for
the methodology's Annexure A weighting, so a wrong one silently reweights the whole rating.
"""
import logging
from datetime import date

from app.core import llm_settings
from app.core.config import settings
from app.esg import prompts

logger = logging.getLogger(__name__)

SECTOR_SYSTEM = prompts.load("stock_exchange_sector")

# The record stored with the analysis. The model supplies only `sector`; the rest is ours.
FIELDS = ("company_name", "stock_exchange", "ticker", "sector", "source", "verification_date")

NOT_VERIFIED = "not verified"
WEB_SOURCE = "Official stock exchange, via web search"
UNVERIFIED_SOURCE = "AI model knowledge - not verified against the exchange"

# A sector name is a few words. Anything longer is an explanation or an error message.
MAX_SECTOR_LENGTH = 80

# `web_search` is the current name; older API surfaces call it `web_search_preview`. Both are
# tried so the lookup does not go dark on an account served the older one.
TOOL_TYPES = ("web_search", "web_search_preview")


def _client():
    from openai import OpenAI

    return OpenAI(api_key=settings.esg_openai_api_key)


def model() -> str:
    """The model used for the web lookup. Its own setting: the tool has to be one this model
    serves, and that is a different question from which model scores the pages."""
    return settings.esg_sector_model or settings.esg_openai_model


def sector_name(answer) -> str | None:
    """The sector in a model's answer, or None when the answer is not one.

    The prompt allows exactly two answers: a sector name or "Not Verified". Everything else
    -- an error string, a paragraph of reasoning, an empty reply -- is rejected rather than
    printed on a rating report as though it were a sector."""
    text = str(answer or "").strip().strip("\"'`").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    name = lines[0].strip("\"'`.").strip()
    lowered = name.lower()
    if (not name or len(name) > MAX_SECTOR_LENGTH or NOT_VERIFIED in lowered
            or lowered.startswith(("an unexpected error", "error", "i ", "sorry"))):
        return None
    return name


def _listing(company_name: str, sector: str, source: str, verified: bool) -> dict:
    return {
        "company_name": company_name, "stock_exchange": "", "ticker": "", "sector": sector,
        "source": source,
        # The day we checked, from our own clock. Blank when nothing was checked against the
        # exchange: a date there would read as a verification that never happened.
        "verification_date": date.today().isoformat() if verified else "",
    }


def _messages(company_name: str) -> list[dict]:
    return [{"role": "system", "content": SECTOR_SYSTEM},
            {"role": "user", "content": f"Company name: {company_name}"}]


def available() -> bool:
    """Whether the web lookup can run: an OpenAI key, and OpenAI as the provider. Bedrock's
    Converse API has no web search."""
    return bool(settings.esg_openai_api_key) and llm_settings.resolve() == llm_settings.OPENAI


def search(company_name: str, client=None) -> dict | None:
    """The sector as read off the web, or None when it could not be verified."""
    company_name = (company_name or "").strip()
    if not company_name or (client is None and not available()):
        return None
    api = client or _client()
    for tool in TOOL_TYPES:
        try:
            response = api.responses.create(model=model(), tools=[{"type": tool}],
                                            input=_messages(company_name))
        except Exception as e:
            logger.warning("sector lookup: %s failed for %s: %s", tool, company_name, e)
            continue
        name = sector_name(getattr(response, "output_text", ""))
        if not name:
            logger.warning("sector lookup: not verified for %s", company_name)
            return None
        logger.info("sector lookup: %s -> %s", company_name, name)
        return _listing(company_name, name, WEB_SOURCE, verified=True)
    return None


def _ask(llm, company_name: str) -> str:
    """One plain-text answer from a scoring model. The OpenAI scoring client forces JSON
    output, which this prompt does not produce, so it is called directly for plain text."""
    if hasattr(llm, "clients"):
        response = llm.clients.chat.completions.create(
            model=llm.model, messages=_messages(company_name), temperature=0.01)
        return response.choices[0].message.content
    return llm.generate_score(f"{SECTOR_SYSTEM}\n\nCompany name: {company_name}")


def from_knowledge(company_name: str, llm=None) -> dict | None:
    """The sector from the scoring model's own knowledge, stored as unverified, or None.

    Goes through whichever model the dashboard has selected, so on AWS it is billed to AWS
    and no OpenAI key is involved."""
    company_name = (company_name or "").strip()
    if not company_name:
        return None
    if llm is None:
        if llm_settings.resolve() == llm_settings.OPENAI and not settings.esg_openai_api_key:
            return None  # no model to ask
        try:
            from app.esg import llm as llm_mod
            llm = llm_mod.get_llm()
        except Exception as e:
            logger.warning("sector from knowledge: no model for %s: %s", company_name, e)
            return None
    try:
        name = sector_name(_ask(llm, company_name))
    except Exception as e:
        logger.warning("sector from knowledge: failed for %s: %s", company_name, e)
        return None
    if not name:
        logger.warning("sector from knowledge: not verified for %s", company_name)
        return None
    logger.info("sector from knowledge: %s -> %s, unverified", company_name, name)
    return _listing(company_name, name, UNVERIFIED_SOURCE, verified=False)


def lookup(company_name: str) -> dict | None:
    """The company's sector: read off the web where a search can run, otherwise from the
    model's own knowledge and stored as unverified. None when neither gives one, and the
    report then shows a blank an analyst can fill."""
    return search(company_name) or from_knowledge(company_name)
