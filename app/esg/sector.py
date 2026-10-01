"""The company's official stock-exchange sector, looked up on the web.

The requirement is the client's (CFC, 2026-09-29): find the company's current listing and
ticker, read the sector the exchange itself assigns, verify the listing is current and the
company is the right one, prefer the primary listing, and keep the source and date so the
answer can be audited. The prompt in app/esg/prompts/stock_exchange_sector.txt writes that
brief as a working instruction rather than repeating his note.

His central rule is what shapes this module:

    "Do not infer or assign the sector using general knowledge or ESG analysis."

A model answering from its training data is doing exactly that, and it would have to invent
`source` and `verification_date` -- the audit trail the prompt exists to create. So this
goes through the Responses API with the `web_search` tool, which actually searches, and an
answer that comes back without a source is discarded rather than stored.

Nothing here guesses. Where the lookup cannot run -- the provider is Bedrock, whose Converse
API has no web search; there is no API key; the search found nothing it could verify -- it
returns None, the sector is left blank and an analyst fills it in. A blank sector is a
smaller error than a confident wrong one: it is also the materiality key for the
methodology's Annexure A weighting, so a wrong sector silently reweights the whole rating.
"""
import json
import logging
import re
from datetime import date

from app.core import llm_settings
from app.core.config import settings
from app.esg import prompts

logger = logging.getLogger(__name__)

SECTOR_SYSTEM = prompts.load("stock_exchange_sector")

# The shape the client's prompt asks for, in his order.
FIELDS = ("company_name", "stock_exchange", "ticker", "sector", "source", "verification_date")

# Without these the answer is not a verified listing, whatever else it contains: no sector is
# no answer, and no source means nothing was actually looked up.
REQUIRED = ("sector", "source")

# `web_search` is the current name; older API surfaces call it `web_search_preview`. Both are
# tried so the lookup does not go dark on an account served the older one.
TOOL_TYPES = ("web_search", "web_search_preview")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _client():
    from openai import OpenAI

    return OpenAI(api_key=settings.esg_openai_api_key)


def model() -> str:
    """The model used for the lookup. Its own setting: the tool has to be one this model
    serves, and that is a different question from which model scores the pages."""
    return settings.esg_sector_model or settings.esg_openai_model


def _parse(text: str) -> dict | None:
    body = _FENCE.sub("", str(text or "")).strip()
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        # The answer may carry prose around the object when the search returns citations.
        match = re.search(r"\{.*\}", body, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def _clean(data: dict, company_name: str) -> dict | None:
    """The client's six fields, or None when the answer is not a verified listing."""
    out = {k: str(data.get(k) or "").strip() for k in FIELDS}
    if any(not out[k] for k in REQUIRED):
        return None
    # The date the lookup was made. Taken from our own clock, not the model's: a
    # verification date is a record of when we checked, and is the one field a model has no
    # standing to report.
    out["verification_date"] = date.today().isoformat()
    out["company_name"] = out["company_name"] or company_name
    return out


def available() -> bool:
    """Whether a lookup can run at all: an OpenAI key, and OpenAI as the provider. Bedrock's
    Converse API has no web search, so on AWS the field is the analyst's to fill."""
    return bool(settings.esg_openai_api_key) and llm_settings.resolve() == llm_settings.OPENAI


def search(company_name: str, client=None) -> dict | None:
    """The listing as read off the web: {company_name, stock_exchange, ticker, sector,
    source, verification_date}, or None when nothing could be verified."""
    company_name = (company_name or "").strip()
    if not company_name or (client is None and not available()):
        return None
    api = client or _client()
    for tool in TOOL_TYPES:
        try:
            response = api.responses.create(
                model=model(),
                tools=[{"type": tool}],
                input=[{"role": "system", "content": SECTOR_SYSTEM},
                       {"role": "user", "content": f"Company name: {company_name}"}],
            )
        except Exception as e:
            logger.warning("sector lookup: %s failed for %s: %s", tool, company_name, e)
            continue
        data = _parse(getattr(response, "output_text", "") or "")
        listing = _clean(data, company_name) if data else None
        if listing:
            logger.info("sector lookup: %s -> %s (%s %s) via %s", company_name,
                        listing["sector"], listing["stock_exchange"], listing["ticker"],
                        listing["source"])
            return listing
        logger.warning("sector lookup: no verified listing for %s", company_name)
        return None
    return None


# --- From the model's own knowledge ------------------------------------------------------
#
# The client's brief asks for a web lookup and rules general knowledge out. Bedrock has no
# web search, so with billing on AWS the sector was simply blank on every report, and the
# decision was taken to accept the model's own knowledge there instead (user, 2026-10-01).
#
# What keeps that honest is the record: an answer from knowledge is stored with the source
# below and no verification date, so it can never be mistaken for one read off the exchange,
# and nothing invents a link or a date to dress it up.
KNOWLEDGE_SYSTEM = prompts.load("sector_from_knowledge")
UNVERIFIED_SOURCE = "AI model knowledge - not verified against the exchange"


def _knowledge_prompt(company_name: str) -> str:
    from app.esg import methodology

    groups = "\n".join(f"- {name}" for name in methodology.SECTOR_MATERIALITY)
    return (f"{KNOWLEDGE_SYSTEM}\n\nSector groups:\n{groups}\n\n"
            f"Company name: {company_name}")


def from_knowledge(company_name: str, llm=None) -> dict | None:
    """The listing from the scoring model's own knowledge, marked as unverified, or None.

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
        data = _parse(llm.generate_score(_knowledge_prompt(company_name)))
    except Exception as e:
        logger.warning("sector from knowledge: failed for %s: %s", company_name, e)
        return None
    sector_name = str((data or {}).get("sector") or "").strip()
    if not sector_name:
        logger.warning("sector from knowledge: model gave no sector for %s", company_name)
        return None
    listing = {
        "company_name": str(data.get("company_name") or "").strip() or company_name,
        "stock_exchange": str(data.get("stock_exchange") or "").strip(),
        "ticker": str(data.get("ticker") or "").strip(),
        "sector": sector_name,
        "source": UNVERIFIED_SOURCE,
        "verification_date": "",
    }
    logger.info("sector from knowledge: %s -> %s (%s %s), unverified", company_name,
                listing["sector"], listing["stock_exchange"], listing["ticker"])
    return listing


def lookup(company_name: str) -> dict | None:
    """The company's listing: read off the web where a search can run, otherwise from the
    model's own knowledge and marked as unverified. None when neither gives a sector, and
    the report then shows a blank an analyst can fill."""
    return search(company_name) or from_knowledge(company_name)
