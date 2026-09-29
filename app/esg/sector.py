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


def lookup(company_name: str, client=None) -> dict | None:
    """{company_name, stock_exchange, ticker, sector, source, verification_date}, or None.

    None is a normal outcome, not an error: it means nothing was verified, and the report
    shows a blank the analyst can fill rather than a sector nobody checked."""
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
