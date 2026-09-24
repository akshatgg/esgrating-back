# Port of esg_score_calculator-master/llm/gpt.py (generate_score only; generate_text and
# get_embedding are unused by the pipeline). Divergences: the model name comes from
# settings, and the same models can be reached through Amazon Bedrock.
from openai import OpenAI

from app.core import llm_settings
from app.core.config import settings


def model_for(provider: str) -> str:
    """The model id for a provider. Bedrock names the same models differently, so each has
    its own setting and neither can be sent to the wrong endpoint."""
    if provider == llm_settings.AWS:
        return llm_settings.bedrock_model()
    return settings.esg_openai_model


class GPTModel:
    def __init__(self, api_key, model, client=None, provider=llm_settings.OPENAI):
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.clients = client if client is not None else OpenAI(api_key=api_key)

    def generate_score(self, text):
        try:
            response = self.clients.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content":text}],
                temperature=0.01,
                presence_penalty=0.5,
                seed=123,
                response_format={ "type": "json_object" }
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"


# The original built one module-level GPTModel at import (helper.py:15). We keep a single
# shared instance, rebuilt only if the configured key/model/provider change. Pipeline code
# calls llm_mod.get_llm() through the module so tests can monkeypatch it.
_llm = None


def get_llm():
    """The scoring model for the configured provider.

    Read on every call: an admin changing the provider or model on the dashboard takes
    effect on the next analysis, not the next restart. Bedrock goes through Converse
    (app/core/bedrock.py), not the OpenAI-compatible endpoint, which serves only the
    gpt-5.6 and gpt-6 families and rejects everything else."""
    global _llm
    provider = llm_settings.resolve()
    key, model = settings.esg_openai_api_key, model_for(provider)
    if _llm is None or (_llm.api_key, _llm.model, _llm.provider) != (key, model, provider):
        if provider == llm_settings.AWS:
            from app.core.bedrock import BedrockModel
            _llm = BedrockModel(model)
        else:
            _llm = GPTModel(key, model, provider=provider)
    return _llm
