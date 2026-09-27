"""LLM providers and the model router.

Ollama is the local default; any OpenAI-compatible API (Groq, Cerebras, OpenAI, ...) works too.
`ModelRouter` is what the graph talks to: it holds the model picked in the dashboard, can switch at
runtime, and falls back to local Ollama when a hosted model fails.

Streaming: providers accept `on_token(text_so_far)`. The callback always gets the CUMULATIVE text, so
a fallback that restarts generation on another model simply overwrites what was shown.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings

log = logging.getLogger("synapse.llm")
THINK = re.compile(r"<think>.*?</think>\s*", re.S)
# Reasoning models spend tokens thinking before they answer; without headroom a 700-token plan budget
# is used up by the thinking and the JSON comes back cut off.
REASONING = re.compile(r"gpt-oss|qwen3|deepseek-r1|reason|magistral", re.I)
OnToken = Callable[[str], None]


class LLMError(Exception):
    def __init__(self, msg: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(msg)
        self.status, self.retry_after = status, retry_after


_DUR = re.compile(r"(?:(\d+)m)?([\d.]+)(ms|s)?")


def _secs(v: str) -> float | None:
    m = _DUR.fullmatch(v.strip())
    return int(m.group(1) or 0) * 60 + float(m.group(2)) / (1000 if m.group(3) == "ms" else 1) if m else None


def _retry_after(headers, body: str = "") -> float | None:
    """Seconds a rate-limited API asks us to wait: the Retry-After header, Groq's "Please try again in
    4.7s" in the error body, or its x-ratelimit-reset-tokens header — in that order."""
    v = _secs(headers.get("retry-after") or "")
    if v is not None:
        return v
    m = re.search(r"try again in ((?:\d+m)?[\d.]+(?:ms|s))", body or "")
    if m:
        return _secs(m.group(1))
    return _secs(headers.get("x-ratelimit-reset-tokens") or "")


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    fallback_from: str | None = None      # set when a hosted model failed and this answer came from the local one


class LLM(Protocol):
    async def chat(self, messages: list[dict], json_schema: dict | None = None, max_tokens: int | None = None) -> LLMResponse: ...


class OllamaLLM:
    streams = True

    def __init__(self, url: str, model: str, timeout: float, num_ctx: int = 8192, keep_alive: str = "30m"):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.num_ctx, self.keep_alive = num_ctx, keep_alive

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        opts = {"temperature": 0.1, "num_ctx": self.num_ctx}
        if max_tokens:
            opts["num_predict"] = max_tokens
        stream = bool(on_token) and not json_schema
        payload = {"model": self.model, "messages": messages, "stream": stream,
                   "keep_alive": self.keep_alive, "options": opts}
        if json_schema:
            payload["format"] = json_schema
        t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                if not stream:
                    r = await c.post(f"{self.url}/api/chat", json=payload)
                    self._check(r.status_code, r.text)
                    d = r.json()
                    text = d["message"]["content"]
                else:
                    text, d = "", {}
                    async with c.stream("POST", f"{self.url}/api/chat", json=payload) as r:
                        if r.status_code != 200:
                            self._check(r.status_code, (await r.aread()).decode(errors="ignore"))
                        async for line in r.aiter_lines():
                            if not line.strip():
                                continue
                            d = json.loads(line)
                            piece = (d.get("message") or {}).get("content") or ""
                            if piece:
                                text += piece
                                on_token(text)
        except httpx.ConnectError as e:
            raise LLMError(f"Ollama not reachable at {self.url}. Run `ollama serve`.") from e
        except httpx.TimeoutException as e:
            raise LLMError(f"Ollama timed out after {self.timeout}s") from e
        return LLMResponse(text=text, provider="ollama", model=self.model,
                           latency_ms=int((time.perf_counter() - t) * 1000),
                           input_tokens=d.get("prompt_eval_count"), output_tokens=d.get("eval_count"))

    def _check(self, status: int, body: str) -> None:
        if status == 404:
            raise LLMError(f"Model '{self.model}' not found. Run `ollama pull {self.model}`.", 404)
        if status != 200:
            raise LLMError(f"Ollama HTTP {status}: {body[:300]}", status)


class OpenAICompatLLM:
    streams = True

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, provider: str = "openai-compat"):
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout
        self.provider = provider

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        payload = {"model": self.model, "messages": messages, "temperature": 0.1}
        if max_tokens:
            payload["max_tokens"] = max_tokens + (2048 if REASONING.search(self.model) else 0)
        if json_schema:
            payload["response_format"] = {"type": "json_object"}
        stream = bool(on_token) and not json_schema
        if stream:
            payload |= {"stream": True, "stream_options": {"include_usage": True}}
        headers = {"Authorization": f"Bearer {self.key}"}
        t = time.perf_counter()
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                if not stream:
                    r = await c.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                    if r.status_code == 400 and json_schema and "json_validate_failed" in r.text:
                        # Groq rejects JSON it can't parse. Hand the attempt back so structured() can ask
                        # this same fast model to repair it, instead of dropping to the slow local model.
                        try:
                            failed = r.json()["error"].get("failed_generation") or ""
                        except (ValueError, KeyError, AttributeError):
                            failed = ""
                        return LLMResponse(text=failed or "{}", provider=self.provider, model=self.model,
                                           latency_ms=int((time.perf_counter() - t) * 1000))
                    if r.status_code != 200:
                        raise LLMError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}", r.status_code,
                                       _retry_after(r.headers, r.text) if r.status_code == 429 else None)
                    d = r.json()
                    text, usage = d["choices"][0]["message"]["content"] or "", d.get("usage") or {}
                else:
                    text = ""
                    async with c.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as r:
                        if r.status_code != 200:
                            body = (await r.aread()).decode(errors="ignore")
                            raise LLMError(f"{self.provider} HTTP {r.status_code}: {body[:300]}", r.status_code,
                                           _retry_after(r.headers, body) if r.status_code == 429 else None)
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            d = json.loads(data)
                            usage = d.get("usage") or (d.get("x_groq") or {}).get("usage") or usage
                            for ch in d.get("choices") or []:
                                piece = (ch.get("delta") or {}).get("content") or ""
                                if piece:
                                    text += piece
                                    on_token(text)
        except httpx.HTTPError as e:
            raise LLMError(f"{self.provider} request failed: {type(e).__name__}: {e}") from e
        return LLMResponse(text=THINK.sub("", text), provider=self.provider, model=self.model,
                           latency_ms=int((time.perf_counter() - t) * 1000),
                           input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))


class FallbackLLM:
    """Try the fast provider first; on failure fall back to the local model so a run never dies
    because a free-tier quota ran out. A rejected key disables the fast provider for the session
    instead of paying a failed round-trip on every call; a rate limit pauses it for a minute."""
    streams = True

    MAX_WAIT = 20.0   # a short rate-limit wait on the hosted model beats a minute of local generation

    def __init__(self, fast: LLM, slow: LLM):
        self.fast, self.slow = fast, slow
        self.fast_off_until = 0.0
        self.last_error: str | None = None

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        why = None
        if time.time() >= self.fast_off_until:
            try:
                try:
                    return await self.fast.chat(messages, json_schema, max_tokens, on_token=on_token)
                except LLMError as e:
                    if e.status != 429 or e.retry_after is None or e.retry_after > self.MAX_WAIT:
                        raise
                    log.info("rate limited; waiting %.1fs for the hosted model", e.retry_after)
                    await asyncio.sleep(e.retry_after + 0.3)
                    return await self.fast.chat(messages, json_schema, max_tokens, on_token=on_token)
            except LLMError as e:
                self.last_error = why = str(e)
                pause = (float("inf") if e.status in (401, 403, 404)
                         else min(e.retry_after or 20.0, 60.0) if e.status == 429 else 0.0)
                self.fast_off_until = time.time() + pause
                log.warning("fast model failed (%s); using local model%s", e,
                            " for the rest of this session" if pause == float("inf") else "")
        else:
            why = f"hosted model paused after: {self.last_error}"
        try:
            r = await self.slow.chat(messages, json_schema, max_tokens, on_token=on_token)
        except LLMError as e:
            raise LLMError(f"{why} — and the local fallback failed too: {e}" if why else str(e), e.status) from e
        return r.model_copy(update={"fallback_from": why}) if why else r


# ---------------- the model router (what the graph and dashboard use) ----------------
RUN_MODEL: contextvars.ContextVar[str | None] = contextvars.ContextVar("synapse_run_model", default=None)
NON_CHAT = re.compile(r"whisper|tts|guard|playai|orpheus|embed|distil|audio|transcri|moderation", re.I)


def _provider_name(base_url: str) -> str:
    host = urlparse(base_url).hostname or "remote"
    if host == "localhost" or re.fullmatch(r"[\d.]+|[\da-f:]+", host):
        return "api"
    for known in ("groq", "cerebras", "openai", "together", "openrouter", "mistral", "deepseek", "fireworks"):
        if known in host:
            return known
    return host.split(".")[-2] if host.count(".") >= 1 else host


class ModelRouter:
    """Holds the model chosen in the dashboard and routes each call to it.

    ids look like "ollama:qwen2.5:7b-instruct" or "groq:llama-3.3-70b-versatile". The choice is saved
    to data/model.json so it survives restarts. A run keeps the model it started with (RUN_MODEL), so
    switching mid-run never mixes two models inside one answer. Hosted models fall back to the local
    Ollama model when they fail."""
    streams = True

    def __init__(self, s: Settings):
        self.s = s
        self.file = s.data_dir / "model.json"
        self._clients: dict[str, LLM] = {}
        self.remote = None
        if s.llm_provider == "openai":
            if not s.openai_api_key:
                raise LLMError("SYNAPSE_OPENAI_API_KEY is required for provider=openai")
            self.remote = ("openai" if "api.openai.com" in s.openai_base_url else _provider_name(s.openai_base_url),
                           s.openai_base_url, s.openai_api_key.get_secret_value())
        elif s.fast_enabled:
            self.remote = (_provider_name(s.fast_base_url), s.fast_base_url, s.fast_api_key.get_secret_value())
        self.default = (f"{self.remote[0]}:{s.fast_model}" if s.llm_provider == "ollama" and self.remote
                        else f"{self.remote[0]}:{s.model}" if self.remote else f"ollama:{s.model}")
        self.local_id = f"ollama:{s.model}" if s.llm_provider == "ollama" else None
        self.choice = self._load() or self.default
        self.last_fallback: str | None = None

    # -- persistence
    def _load(self) -> str | None:
        try:
            v = json.loads(Path(self.file).read_text()).get("id")
        except (OSError, ValueError, AttributeError):
            return None
        return v if isinstance(v, str) and self._valid_prefix(v) else None

    def _valid_prefix(self, mid: str) -> bool:
        prov = mid.split(":", 1)[0]
        return (prov == "ollama" and self.s.llm_provider == "ollama") or bool(self.remote and prov == self.remote[0])

    def select(self, mid: str) -> dict:
        if ":" not in mid or not self._valid_prefix(mid):
            raise ValueError(f"unknown model {mid!r}")
        self.choice = mid
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"id": mid}))
        return self.current()

    # -- description
    def current(self, mid: str | None = None) -> dict:
        mid = mid or self.choice
        prov, name = mid.split(":", 1)
        return {"id": mid, "provider": prov, "model": name, "local": prov == "ollama"}

    async def available(self) -> list[dict]:
        """Every chat model this Synapse can use right now: pulled Ollama models + the hosted provider's list."""
        out: list[dict] = []
        if self.s.llm_provider == "ollama":
            try:
                async with httpx.AsyncClient(timeout=4) as c:
                    r = await c.get(f"{self.s.ollama_url.rstrip('/')}/api/tags")
                for m in (r.json().get("models") or []) if r.status_code == 200 else []:
                    name = m.get("name") or m.get("model")
                    if name and not NON_CHAT.search(name):
                        det = m.get("details") or {}
                        out.append(self.current(f"ollama:{name}") | {
                            "size": det.get("parameter_size"), "note": "on this machine"})
            except (httpx.HTTPError, ValueError):
                pass
            if self.local_id and not any(m["id"] == self.local_id for m in out):
                out.append(self.current(self.local_id) | {"note": "configured (Ollama not reachable)", "offline": True})
        if self.remote:
            prov, base, key = self.remote
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    r = await c.get(f"{base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"})
                ids = sorted({m["id"] for m in r.json().get("data", []) if not NON_CHAT.search(m.get("id", ""))}) \
                    if r.status_code == 200 else []
                err = None if r.status_code == 200 else f"HTTP {r.status_code}"
            except (httpx.HTTPError, ValueError, KeyError) as e:
                ids, err = [], type(e).__name__
            configured = self.s.fast_model if self.s.llm_provider == "ollama" else self.s.model
            for mid in ids or [configured]:
                out.append(self.current(f"{prov}:{mid}") | {
                    "note": "hosted" if not err else f"hosted · list unavailable ({err})",
                    "offline": bool(err and "401" in err)})
        if not any(m["id"] == self.choice for m in out):
            out.insert(0, self.current() | {"note": "selected"})
        return out

    # -- calling
    def _client(self, mid: str) -> LLM:
        if mid not in self._clients:
            prov, name = mid.split(":", 1)
            if prov == "ollama":
                self._clients[mid] = OllamaLLM(self.s.ollama_url, name, self.s.llm_timeout, self.s.num_ctx, self.s.keep_alive)
            else:
                _, base, key = self.remote
                hosted = OpenAICompatLLM(base, key, name, min(self.s.llm_timeout, 90) if self.local_id else self.s.llm_timeout, prov)
                self._clients[mid] = FallbackLLM(hosted, self._client(self.local_id)) if self.local_id else hosted
        return self._clients[mid]

    def run_choice(self) -> str:
        return RUN_MODEL.get() or self.choice

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        client = self._client(self.run_choice())
        r = await client.chat(messages, json_schema, max_tokens, on_token=on_token)
        self.last_fallback = getattr(client, "last_error", None) if r.provider == "ollama" and not self.current(self.run_choice())["local"] else None
        return r

    def crew_spec(self) -> dict:
        """What CrewAI should use: the same model as the rest of the run."""
        cur = self.current(self.run_choice())
        if cur["local"]:
            return {"provider": "ollama", "model": cur["model"], "base_url": self.s.ollama_url, "api_key": None}
        _, base, key = self.remote
        return {"provider": cur["provider"], "model": cur["model"], "base_url": base, "api_key": key}


def build_llm(s: Settings) -> ModelRouter:
    return ModelRouter(s)


def inline_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema with $refs inlined (grammar-constrained JSON modes handle this more reliably)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(n):
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(defs[n["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in n.items()}
        if isinstance(n, list):
            return [walk(x) for x in n]
        return n

    return walk(schema)


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


T = TypeVar("T", bound=BaseModel)


async def structured(llm: LLM, messages: list[dict], model: type[T], retries: int = 1,
               max_tokens: int | None = None) -> tuple[T, list[LLMResponse]]:
    """Ask for JSON matching `model`. On malformed output, feed the error back and retry (bounded)."""
    schema = inline_schema(model)
    msgs = [{"role": "system", "content": "Respond with JSON only, matching this JSON schema:\n" + json.dumps(schema)}, *messages]
    calls, last_err = [], ""
    for _ in range(retries + 1):
        resp = await llm.chat(msgs, json_schema=schema, max_tokens=max_tokens)
        calls.append(resp)
        try:
            return model.model_validate(json.loads(_extract_json(resp.text))), calls
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = str(e)[:800]
            msgs += [{"role": "assistant", "content": resp.text},
                     {"role": "user", "content": f"That output was invalid: {last_err}\nReturn corrected JSON only."}]
    raise LLMError(f"Malformed model output after {retries + 1} attempts: {last_err}")
