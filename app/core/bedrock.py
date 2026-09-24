"""Calling a model on Amazon Bedrock, and listing the ones this account may actually use.

Bedrock has two front doors and they do not serve the same models. The OpenAI-compatible
endpoint (bedrock-mantle) serves only the gpt-5.6 and gpt-6 families; everything else --
including gpt-oss, Nova, Llama, Mistral, Qwen -- answers "isn't supported on this
endpoint". The native Converse API serves them all. So this goes through Converse.

Being listed is not the same as being usable. A model appears in list-foundation-models
whether or not the account has accepted its agreement, and invoking one that has not been
accepted fails with "not available for this account" -- which is what took production down
on 2026-09-24. usable_models() therefore asks get-foundation-model-availability, and the
dashboard offers only what comes back clean.
"""
import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

# usable_models() asks Bedrock about every model one at a time, which takes seconds. What
# an account may invoke changes when someone accepts an agreement -- rarely -- so the
# answer is held briefly rather than rebuilt on every dashboard load.
_CACHE_TTL = 300.0
_cache: tuple[float, list[dict]] | None = None

# Embeddings, image and video models cannot answer a scoring prompt.
_SKIP = ("embed", "titan-image", "pegasus", "rerank", "canvas", "reranker")


def _runtime():
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.bedrock_region)


def _control():
    import boto3

    return boto3.client("bedrock", region_name=settings.bedrock_region)


def usable_models(refresh: bool = False) -> list[dict]:
    """[{id, name, provider}] for the text models this account may invoke here.

    Only models whose agreement, authorization, entitlement and region all come back
    available -- the four things that have to be true before a call works. An error
    (no credentials, no permission to list) gives [], so the dashboard shows nothing to
    pick rather than a list that would fail on use.

    Held for _CACHE_TTL seconds; pass refresh=True after accepting a new agreement."""
    global _cache
    if not refresh and _cache and (time.monotonic() - _cache[0]) < _CACHE_TTL:
        return _cache[1]
    try:
        bedrock = _control()
        summaries = bedrock.list_foundation_models().get("modelSummaries", [])
    except Exception as e:
        logger.error("bedrock: cannot list models: %s", e)
        return []   # not cached: a transient failure must not hide the list for 5 minutes

    out, seen = [], set()
    for m in summaries:
        mid = m.get("modelId", "")
        if mid in seen or any(s in mid.lower() for s in _SKIP):
            continue
        if "TEXT" not in (m.get("outputModalities") or []):
            continue
        if not ({"ON_DEMAND", "INFERENCE_PROFILE"} & set(m.get("inferenceTypesSupported") or [])):
            continue
        seen.add(mid)
        try:
            a = bedrock.get_foundation_model_availability(modelId=mid)
        except Exception:
            continue
        if (a.get("agreementAvailability", {}).get("status") == "AVAILABLE"
                and a.get("authorizationStatus") == "AUTHORIZED"
                and a.get("entitlementAvailability") == "AVAILABLE"
                and a.get("regionAvailability") == "AVAILABLE"):
            out.append({"id": mid, "name": m.get("modelName") or mid,
                        "provider": m.get("providerName") or ""})
    out.sort(key=lambda r: (r["provider"].lower(), r["name"].lower()))
    _cache = (time.monotonic(), out)
    return out


class BedrockModel:
    """The same interface as app/esg/llm.py GPTModel: generate_score(text) -> str.

    Errors are returned as text rather than raised, exactly as the OpenAI path does, so a
    failed page is skipped and the run continues."""

    def __init__(self, model: str, client=None):
        self.model = model
        self.provider = "aws"
        self.api_key = ""          # none is used; kept so get_llm can compare instances
        self._client = client

    def _rt(self):
        if self._client is None:
            self._client = _runtime()
        return self._client

    def generate_score(self, text):
        try:
            r = self._rt().converse(
                modelId=self.model,
                messages=[{"role": "user", "content": [{"text": text}]}],
                # Matching the OpenAI path: near-deterministic, and enough room for a page
                # of KPI findings. Converse has no seed and no JSON mode, so the prompt's
                # own "Return JSON" instruction is what shapes the answer; app/core/
                # answers.py already strips a ```json fence if one comes back.
                inferenceConfig={"temperature": 0.01, "maxTokens": 8192},
                # serviceTier is deliberately not set, which means standard. Flex is half
                # the price but queues for minutes per call with a one-hour timeout: on
                # 1,554 calls that turns a 9-minute report into hours, and a call that
                # times out loses that page's evidence silently (user, 2026-09-24).
            )
            parts = r.get("output", {}).get("message", {}).get("content", [])
            return "".join(p.get("text", "") for p in parts)
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

    # --- the BFSI client's interface (app/bfsi/openai_client.py OpenAiJson) -------------
    def json(self, user: str, system: str = "") -> dict | None:
        """One call, a parsed JSON object or None."""
        from app.core.answers import parse_answer

        prompt = f"{system}\n\n{user}" if system else user
        return parse_answer(self.generate_score(prompt))

    def batch(self, prompts: dict, concurrency: int = 8) -> dict:
        """{key: parsed answer} for many prompts at once, the same shape and concurrency
        the BFSI port uses. A prompt whose answer could not be read is left out, exactly as
        the OpenAI client leaves out a unit whose retries were exhausted."""
        import concurrent.futures

        if not prompts:
            return {}
        out = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(self.json, text): key for key, text in prompts.items()}
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    answer = future.result()
                except Exception as e:
                    logger.error("bedrock: batch item %s failed: %s", key, e)
                    continue
                if answer is not None:
                    out[key] = answer
        return out
