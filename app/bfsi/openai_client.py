# Port of the request layer of bfsi-calculator/lib/openai.php (see docs/analysis/bfsi.md
# §4c): bfsi_openai_payload / _handle / _parse / _assert_recoverable / bfsi_openai_json /
# bfsi_openai_json_batch. curl becomes httpx and curl_multi becomes a ThreadPoolExecutor;
# the retry counts, sleep lengths, wave size, fatal-error rules and messages are unchanged.
import concurrent.futures
import json as jsonlib
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# How many scoring requests to keep in flight at once (openai.php:13).
BFSI_CONCURRENCY = 8

# curl's CURLOPT_TIMEOUT => 120 is a whole-request budget; httpx applies the value to
# each phase (connect/read/write/pool), which is the closest single-number equivalent.
TIMEOUT = 120.0


class BfsiOpenAiFatal(RuntimeError):
    """Raised for OpenAI errors that retrying/continuing cannot fix (invalid key, no
    quota). Callers abort the whole analysis immediately so the request fails fast with
    a clear message instead of hanging on every chunk."""


class OpenAiJson:
    def __init__(self, api_key: str, model: str, transport=None):
        self.api_key = api_key
        self.model = model
        self._transport = transport
        # Injectable so tests do not really wait out the 2s/4s backoff.
        self._sleep = time.sleep

    # --- bfsi_openai_payload -------------------------------------------------
    def _payload(self, system: str, user: str) -> str:
        """Request body for one chat-completion call.

        An empty ``system`` sends the user message alone -- the category-scoring calls do
        this to match the ESG calculator, which passes one user message and no system
        message. temperature/seed mirror the ESG calculator (utils/helper.py) so both
        tools score the same report identically. Its presence_penalty=0.5 is deliberately
        NOT copied: it penalises repeated tokens, and JSON keys repeat on every response.
        """
        if not self.api_key:
            raise RuntimeError("OpenAI key not configured")
        messages = []
        if system != "":
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return jsonlib.dumps({
            "model": self.model,
            "temperature": 0.01,
            "seed": 123,
            "response_format": {"type": "json_object"},
            "messages": messages,
        })

    # --- bfsi_openai_handle + curl_exec -------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=TIMEOUT, transport=self._transport)

    def _send(self, client: httpx.Client, payload: str) -> tuple[int, str | None]:
        """(http status, body). A transport failure is curl's code 0 with no body."""
        try:
            r = client.post(
                OPENAI_URL,
                content=payload.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.api_key,
                },
            )
            return r.status_code, r.text
        except httpx.HTTPError as e:
            logger.error("bfsi: OpenAI request failed: %s", e)
            return 0, None

    # --- bfsi_openai_parse ---------------------------------------------------
    @staticmethod
    def _parse(resp: str | None) -> dict | None:
        """The JSON object inside a 200 response, or None if the body was unusable."""
        if not resp:
            return None
        try:
            body = jsonlib.loads(resp)
        except ValueError:
            return None
        content = ""
        if isinstance(body, dict):
            choices = body.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and message.get("content") is not None:
                    content = message["content"]
        try:
            parsed = jsonlib.loads(content)
        except (ValueError, TypeError):
            return None
        # PHP's is_array() also accepts a JSON list; a json_object response_format never
        # returns one, and every caller indexes it by name, so we require an object.
        return parsed if isinstance(parsed, dict) else None

    # --- bfsi_openai_assert_recoverable -------------------------------------
    @staticmethod
    def _assert_recoverable(code: int, resp: str | None) -> None:
        """Abort the whole analysis on errors that retrying cannot fix (bad key, no
        quota), so the request fails fast with a clear message instead of grinding
        through every page and timing out the browser."""
        err_type = ""
        if resp:
            try:
                j = jsonlib.loads(resp)
            except ValueError:
                j = None
            if isinstance(j, dict) and isinstance(j.get("error"), dict):
                err = j["error"]
                # PHP: $j['error']['code'] ?? ($j['error']['type'] ?? '')
                err_type = err["code"] if err.get("code") is not None else (
                    err["type"] if err.get("type") is not None else "")
        if code == 401 or code == 403 or err_type == "insufficient_quota":
            if code == 401:
                msg = "OpenAI API key is invalid."
            elif err_type == "insufficient_quota":
                msg = ("OpenAI account has no available quota/credits — add billing at "
                       "platform.openai.com and try again.")
            else:
                msg = f"OpenAI rejected the request (HTTP {code})."
            raise BfsiOpenAiFatal(msg)

    # --- bfsi_openai_json ----------------------------------------------------
    def json(self, user: str, system: str = "") -> dict:
        """One call, 3 tries, sleeping 2s then 4s. A 200 whose body does not parse to a
        JSON object counts as a failed try, exactly as in the PHP loop."""
        payload = self._payload(system, user)
        code = 0
        with self._client() as client:
            for attempt in range(3):
                code, resp = self._send(client, payload)
                if code == 200:
                    parsed = self._parse(resp)
                    if parsed is not None:
                        return parsed
                self._assert_recoverable(code, resp or None)
                if attempt < 2:
                    self._sleep(2 * (attempt + 1))
        raise RuntimeError(f"OpenAI call failed after retries (HTTP {code})")

    # --- bfsi_openai_json_batch ---------------------------------------------
    def batch(self, prompts: dict, concurrency: int = BFSI_CONCURRENCY) -> dict:
        """Run many scoring calls concurrently, mirroring the ESG calculator's
        ThreadPoolExecutor (helper.py). Keys are preserved; a unit whose 3 tries all
        failed comes back None; a fatal error aborts everything.

        :param prompts: keyed prompts, sent as a user message with no system message
        """
        results = {key: None for key in prompts}
        queue = [{"key": key, "payload": self._payload("", prompt), "try": 0}
                 for key, prompt in prompts.items()]
        wave_size = max(1, concurrency)

        with self._client() as client:
            while queue:
                wave, queue = queue[:wave_size], queue[wave_size:]
                with concurrent.futures.ThreadPoolExecutor(max_workers=wave_size) as pool:
                    responses = list(pool.map(
                        lambda item: self._send(client, item["payload"]), wave))

                retry = []
                for item, (code, resp) in zip(wave, responses):
                    if code == 200:
                        parsed = self._parse(resp)
                        if parsed is not None:
                            results[item["key"]] = parsed
                            continue
                    self._assert_recoverable(code, resp or None)  # aborts everything
                    item["try"] += 1
                    if item["try"] < 3:
                        retry.append(item)
                    else:
                        logger.error("bfsi: page unit %s failed after retries (HTTP %s)",
                                     item["key"], code)

                # Failed units go to the back of the queue after a short pause -- usually a 429.
                if retry:
                    self._sleep(2)
                    queue.extend(retry)
        return results


# One shared client, rebuilt only if the configured key/model change (mirrors
# app/esg/llm.py::get_llm). Pipeline code calls get_client() through the module so
# tests can monkeypatch it.
_client: OpenAiJson | None = None


def get_client() -> OpenAiJson:
    global _client
    key, model = settings.bfsi_openai_api_key, settings.bfsi_openai_model
    if _client is None or (_client.api_key, _client.model) != (key, model):
        _client = OpenAiJson(key, model)
    return _client
