# app/settings_admin/router.py
# Admin settings that apply to both calculators. Today that is one: which account the AI
# usage is billed to (app/core/llm_settings.py) -- AWS through Amazon Bedrock, or the
# OpenAI API directly. It is read on every scoring call, so a change here takes effect on
# the next analysis rather than the next deploy.
from fastapi import APIRouter, Body, Depends

from app.auth.deps import require_admin
from app.core import bedrock, llm_settings

router = APIRouter(prefix="/api/admin/settings", tags=["admin-settings"])


def _state() -> dict:
    """What the dashboard shows: the provider in force, whether an admin chose it, the
    options, and whether AWS can actually be used from this server."""
    chosen = llm_settings.stored()
    aws_ready = llm_settings.aws_credentials_available()
    # Only offered when AWS is in play: listing them asks Bedrock about every model, which
    # is wasted work on a deployment that bills OpenAI.
    models = bedrock.usable_models() if aws_ready else []
    return {
        "provider": llm_settings.provider(),
        "effective": llm_settings.resolve(),
        "chosen_by_admin": chosen is not None,
        "default": llm_settings.DEFAULT,
        "aws_available": aws_ready,
        "options": [{"value": v, "label": llm_settings.LABELS[v]} for v in llm_settings.PROVIDERS],
        "bedrock_model": llm_settings.bedrock_model(),
        "bedrock_models": models,
    }


@router.get("/llm-provider")
def get_llm_provider(admin: str = Depends(require_admin)):
    return _state()


@router.put("/llm-provider")
def put_llm_provider(provider: str = Body(..., embed=True), admin: str = Depends(require_admin)):
    """Set which account pays for the AI. Invalid values raise UserError -> 422."""
    llm_settings.set_provider(provider, admin)
    return _state()


@router.put("/bedrock-model")
def put_bedrock_model(model: str = Body(..., embed=True), admin: str = Depends(require_admin)):
    """Set which Bedrock model the analysis uses. Only a model this account may invoke is
    accepted, so a bad choice fails here rather than on every page of the next report."""
    llm_settings.set_bedrock_model(model, admin)
    return _state()
