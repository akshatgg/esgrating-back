# Port of esg_score_calculator-master/llm/gpt.py (generate_score only; generate_text and
# get_embedding are unused by the pipeline). Only divergence: model name from settings.
from openai import OpenAI

from app.core.config import settings


class GPTModel:
    def __init__(self, api_key, model, client=None):
        self.api_key = api_key
        self.model = model
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
# shared instance, rebuilt only if the configured key/model change. Pipeline code calls
# llm_mod.get_llm() through the module so tests can monkeypatch it.
_llm = None


def get_llm() -> GPTModel:
    global _llm
    key, model = settings.esg_openai_api_key, settings.esg_openai_model
    if _llm is None or (_llm.api_key, _llm.model) != (key, model):
        _llm = GPTModel(key, model)
    return _llm
