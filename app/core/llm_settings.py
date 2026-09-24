"""Which account the AI usage is billed to, set by an admin and shared by both calculators.

"aws" sends the same OpenAI models through Amazon Bedrock, authenticating with the AWS
credential chain -- the IAM keys deploy.sh already writes into the container -- so no
OpenAI key is involved and the usage draws on AWS. "openai" calls the OpenAI API directly
with the configured key.

It is a database setting rather than an environment variable so an admin can move the
billing without a deploy, and it is read on every call so the change takes effect on the
next analysis rather than the next restart.

The default is "openai" until Bedrock is proven against a real report: see DEFAULT below.
resolve() also falls back to "openai" when the AWS credential chain is empty, so a server
with no credentials keeps rating instead of failing every call.
"""
import logging

from app.core.db import get_db
from app.core.errors import UserError

logger = logging.getLogger(__name__)

COLLECTION = "app_settings"
KEY = "llm_provider"
MODEL_KEY = "bedrock_model"

AWS = "aws"
OPENAI = "openai"
PROVIDERS = (AWS, OPENAI)
# OpenAI until Bedrock is proven end to end. AWS was the default briefly and it took
# production down: the account has no model access for gpt-5.6 ("not available for this
# account"), and gpt-oss is not served on the OpenAI-compatible endpoint this code uses,
# so every scoring call failed and no report could be produced. An admin can still pick
# AWS on the dashboard; it must not be what a fresh deployment does on its own.
DEFAULT = OPENAI

LABELS = {
    AWS: "AWS (Amazon Bedrock)",
    OPENAI: "OpenAI",
}


def stored() -> str | None:
    """The provider an admin chose, or None when none has been chosen."""
    doc = get_db()[COLLECTION].find_one({"key": KEY})
    value = (doc or {}).get("value")
    return value if value in PROVIDERS else None


def provider() -> str:
    """The configured provider, defaulting to AWS."""
    return stored() or DEFAULT


def bedrock_model() -> str:
    """The Bedrock model an admin picked, else the configured default.

    Which models an account may actually invoke differs by account and region, so this is a
    setting rather than a constant -- see app/core/bedrock.py usable_models()."""
    from app.core.config import settings

    doc = get_db()[COLLECTION].find_one({"key": MODEL_KEY})
    value = (doc or {}).get("value")
    return value if isinstance(value, str) and value.strip() else settings.esg_bedrock_model


def set_bedrock_model(value: str, admin: str = "") -> str:
    """Store the Bedrock model an admin picked. Only a model this account may invoke is
    accepted: storing one it cannot would fail every call at analysis time instead of here."""
    from app.core import bedrock

    value = (value or "").strip()
    allowed = {m["id"] for m in bedrock.usable_models()}
    if value not in allowed:
        raise UserError("That model is not available to this AWS account in this region.")
    from datetime import datetime, timezone
    get_db()[COLLECTION].update_one(
        {"key": MODEL_KEY},
        {"$set": {"value": value, "updated_at": datetime.now(timezone.utc), "updated_by": admin}},
        upsert=True,
    )
    logger.info("bedrock model set to %s by %s", value, admin or "?")
    return value


def set_provider(value: str, admin: str = "") -> str:
    """Store the provider an admin picked. Raises UserError on anything else."""
    value = (value or "").strip().lower()
    if value not in PROVIDERS:
        raise UserError(f"Provider must be one of: {', '.join(PROVIDERS)}.")
    from datetime import datetime, timezone
    get_db()[COLLECTION].update_one(
        {"key": KEY},
        {"$set": {"value": value, "updated_at": datetime.now(timezone.utc), "updated_by": admin}},
        upsert=True,
    )
    logger.info("llm provider set to %s by %s", value, admin or "?")
    return value


def aws_credentials_available() -> bool:
    """Whether the AWS credential chain can produce credentials here. False on a machine
    with no role, no keys and no profile -- where every Bedrock call would fail."""
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def resolve() -> str:
    """The provider to actually use for a call.

    AWS unless it was turned off, or unless this machine has no AWS credentials at all, in
    which case OpenAI is used and the reason is logged. Never raises: a call has to go
    somewhere."""
    chosen = provider()
    if chosen == AWS and not aws_credentials_available():
        logger.error("llm provider is 'aws' but no AWS credentials are available; "
                     "falling back to the OpenAI API for this call")
        return OPENAI
    return chosen
