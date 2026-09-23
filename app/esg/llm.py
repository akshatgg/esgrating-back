# Port of esg_score_calculator-master/llm/gpt.py (generate_score only; generate_text and
# get_embedding are unused by the pipeline). Divergences: the model name comes from
# settings, and the same models can be reached through Amazon Bedrock.
from openai import OpenAI

from app.core import llm_settings
from app.core.config import settings


def _client(api_key: str, provider: str):
    """The OpenAI client for a provider.

    llm_settings.AWS reaches the same OpenAI models through Amazon Bedrock, authenticating
    with the AWS credential chain (api_key=None) -- on the server that is the IAM user
    whose keys deploy.sh already writes into the container, so no OpenAI key is involved
    and the usage draws on AWS. Otherwise it is the OpenAI API, unchanged."""
    if provider == llm_settings.AWS:
        from openai.providers import bedrock
        return OpenAI(provider=bedrock(region=settings.bedrock_region, api_key=None))
    return OpenAI(api_key=api_key)


def model_for(provider: str) -> str:
    """The model id for a provider. Bedrock names the same models differently, so each has
    its own setting and neither can be sent to the wrong endpoint."""
    if provider == llm_settings.AWS:
        return settings.esg_bedrock_model
    return settings.esg_openai_model


class GPTModel:
    def __init__(self, api_key, model, client=None, provider=llm_settings.OPENAI):
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.clients = client if client is not None else _client(api_key, provider)

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


def get_llm() -> GPTModel:
    global _llm
    # Read on every call: an admin changing the provider on the dashboard takes effect on
    # the next analysis, not the next restart.
    provider = llm_settings.resolve()
    key, model = settings.esg_openai_api_key, model_for(provider)
    if _llm is None or (_llm.api_key, _llm.model, _llm.provider) != (key, model, provider):
        _llm = GPTModel(key, model, provider=provider)
    return _llm
