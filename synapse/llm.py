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

try:
    from json_repair import repair_json
except ImportError:          # optional: without it, malformed JSON falls back to asking the model again
    repair_json = None

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

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, provider: str = "openai-compat",
                 reasoning_effort: str | None = None):
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout
        self.provider, self.reasoning_effort = provider, reasoning_effort

    def think_room(self) -> int:
        """Extra tokens for a reasoning model's thinking. Kept tight on purpose: Groq counts the max_tokens a
        request DECLARES against the 8k-a-minute free tier, so a generous ceiling rate-limits a fresh minute."""
        return {"low": 384, "medium": 1024}.get(self.reasoning_effort or "", 2048)

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        payload = {"model": self.model, "messages": messages, "temperature": 0.1}
        if "gpt-oss" in self.model and self.reasoning_effort:
            # Default effort thinks for ~1,500 tokens per plan. On Groq's free tier (8k tokens/minute) that alone
            # forced a minute-long wait or a fall back to the local model mid-run.
            payload["reasoning_effort"] = self.reasoning_effort
        if max_tokens:
            payload["max_tokens"] = max_tokens + (self.think_room() if REASONING.search(self.model) else 0)
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
    """Hosted model first, then a second hosted model, then the local one, so a run never dies because a
    free-tier quota ran out. Groq's limits are per model: when gpt-oss-120b is rate-limited, gpt-oss-20b
    usually isn't, and switching costs nothing while waiting costs up to a minute. A rejected key disables
    the hosted provider for the session; a rate-limited model is skipped until its limit resets."""
    streams = True

    SHORT_WAIT = 3.0  # just retry the same model
    MAX_WAIT = 45.0   # longest wait for a hosted model before using the local one (~60s per local plan)

    def __init__(self, fast: LLM, slow: LLM | None, backup: LLM | None = None):
        self.fast, self.slow, self.backup = fast, slow, backup
        self.off_until: dict[int, float] = {}
        self.last_error: str | None = None

    @property
    def fast_off_until(self) -> float:        # kept for callers that read the primary model's pause
        return self.off_until.get(id(self.fast), 0.0)

    def _notify(self, secs: float, note: str | None = None) -> None:
        hook = WAIT_HOOK.get()
        if hook:
            hook(secs) if note is None else hook(secs, note)

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token: OnToken | None = None):
        args = (messages, json_schema, max_tokens)
        why, waits = None, []                       # waits: (seconds, model) for rate-limited hosted models
        hosted = [m for m in (self.fast, self.backup) if m is not None]
        for i, m in enumerate(hosted):
            if time.time() < self.off_until.get(id(m), 0.0):
                why = why or f"{getattr(m, 'model', 'hosted model')} paused after: {self.last_error}"
                continue
            try:
                try:
                    return self._tag(await m.chat(*args, on_token=on_token), why)
                except LLMError as e:
                    if e.status == 429 and e.retry_after is not None and e.retry_after <= self.SHORT_WAIT:
                        self._notify(e.retry_after)
                        await asyncio.sleep(e.retry_after + 0.2)
                        return self._tag(await m.chat(*args, on_token=on_token), why)
                    raise
            except LLMError as e:
                self.last_error = why = str(e)
                name = getattr(m, "model", "hosted model")
                if e.status in (401, 403):            # the key is shared, so the other hosted model fails too
                    for x in hosted:
                        self.off_until[id(x)] = float("inf")
                    log.warning("hosted provider rejected the key (%s); using the local model from now on", e)
                    break
                if e.status == 429:
                    ra = e.retry_after if e.retry_after is not None else 20.0
                    self.off_until[id(m)] = time.time() + min(ra, 600.0)
                    waits.append((ra, m))
                    nxt = hosted[i + 1] if i + 1 < len(hosted) else None
                    if nxt is not None:
                        self._notify(0, f"{name} hit its free-tier limit; using {getattr(nxt, 'model', 'the backup model')}")
                    log.warning("%s rate limited (retry in %.0fs): %s", name, ra, str(e)[:240])
                    continue
                self.off_until[id(m)] = time.time() + (float("inf") if e.status == 404 else 0.0)
                log.warning("hosted model %s failed (%s)", name, e)
        if waits:                                    # every hosted model is rate-limited: wait for the soonest
            ra, m = min(waits, key=lambda w: w[0])
            if ra <= self.MAX_WAIT:
                self._notify(ra)
                await asyncio.sleep(ra + 0.3)
                try:
                    r = await m.chat(*args, on_token=on_token)
                    self.off_until[id(m)] = 0.0
                    return r
                except LLMError as e:
                    self.last_error = why = str(e)
        if self.slow is None:
            raise LLMError(why or "no model available", 429 if waits else None)
        try:
            r = await self.slow.chat(*args, on_token=on_token)
        except LLMError as e:
            raise LLMError(f"{why} — and the local fallback failed too: {e}" if why else str(e), e.status) from e
        return r.model_copy(update={"fallback_from": why}) if why else r

    @staticmethod
    def _tag(r, why):
        return r.model_copy(update={"fallback_from": why}) if why else r


# ---------------- the model router (what the graph and dashboard use) ----------------
RUN_MODEL: contextvars.ContextVar[str | None] = contextvars.ContextVar("synapse_run_model", default=None)
# Called with the seconds left before a rate-limited call is retried, so the dashboard can say why it paused.
WAIT_HOOK: contextvars.ContextVar = contextvars.ContextVar("synapse_wait_hook", default=None)
# Groq rate-limits each model separately: when one hits its limit, its sibling usually still has room.
BACKUP_MODEL = {"openai/gpt-oss-120b": "openai/gpt-oss-20b", "openai/gpt-oss-20b": "openai/gpt-oss-120b"}
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
                mk = lambda n: OpenAICompatLLM(base, key, n, min(self.s.llm_timeout, 90) if self.local_id else self.s.llm_timeout,  # noqa: E731
                                               prov, reasoning_effort=self.s.reasoning_effort)
                spare = self.s.fast_backup_model or BACKUP_MODEL.get(name)
                self._clients[mid] = FallbackLLM(mk(name), self._client(self.local_id) if self.local_id else None,
                                                 backup=mk(spare) if spare and spare != name else None)
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


def _loads(text: str):
    """Parse model JSON. gpt-oss often writes unescaped quotes inside strings (prompt: 'save as "AI Topics"'),
    which failed whole plans twice in a row; a repair pass fixes those without another model call."""
    raw = _extract_json(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if repair_json is None:
            raise
        fixed = repair_json(raw, return_objects=True)
        if not isinstance(fixed, dict) or not fixed:
            raise
        return fixed


async def structured(llm: LLM, messages: list[dict], model: type[T], retries: int = 1,
               max_tokens: int | None = None) -> tuple[T, list[LLMResponse]]:
    """Ask for JSON matching `model`. On malformed output, feed the error back and retry (bounded)."""
    schema = inline_schema(model)
    msgs = [{"role": "system", "content": "Respond with JSON only, matching this JSON schema:\n" + json.dumps(schema)}, *messages]
    calls, last_err = [], ""
    for attempt in range(retries + 1):
        # a second try gets more room: the usual cause of broken JSON is an answer cut off at max_tokens
        cap = int(max_tokens * 1.6) if max_tokens and attempt else max_tokens
        resp = await llm.chat(msgs, json_schema=schema, max_tokens=cap)
        calls.append(resp)
        try:
            return model.model_validate(_loads(resp.text)), calls
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = _short_error(e)
            msgs += [{"role": "assistant", "content": resp.text},
                     {"role": "user", "content": f"That output was invalid: {last_err}\nReturn the complete, corrected JSON only."}]
    raise LLMError(f"the model's answer was incomplete or malformed twice ({last_err}). Please try again.")


def _short_error(e: Exception) -> str:
    """One readable line instead of pydantic's multi-paragraph dump (it was shown to the user verbatim)."""
    if isinstance(e, ValidationError):
        errs = e.errors()
        where = ".".join(str(x) for x in errs[0]["loc"]) if errs else "?"
        return f"{len(errs)} problem(s), first at {where}: {errs[0]['msg'] if errs else e}"
    return str(e)[:300]
