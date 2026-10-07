"""Model configuration, switching, fallback, streaming and dates.

A real HTTP server (httpx MockTransport would skip the streaming parser) stands in for Ollama and an
OpenAI-compatible host, so the providers' actual request/stream code runs."""
import asyncio
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synapse.config import Settings
from synapse.llm import FallbackLLM, LLMError, ModelRouter, OllamaLLM, OpenAICompatLLM
from synapse.runtime import Runtime
from tests.conftest import ScriptedLLM, gen, plan

SEEN: list[dict] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": "qwen2.5:7b-instruct", "details": {"parameter_size": "7.6B"}},
                                               {"name": "nomic-embed-text:latest"}]})
        if self.path == "/v1/models":
            if self.headers.get("Authorization") != "Bearer good":
                return self._send(401, {"error": "bad key"})
            return self._send(200, {"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "whisper-large-v3"}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "model": body.get("model"), "stream": body.get("stream"),
                     "auth": self.headers.get("Authorization"), "messages": body.get("messages"),
                     "reasoning_effort": body.get("reasoning_effort")})
        text = "hello streamed world"
        if self.path == "/api/chat":
            if body.get("stream"):
                lines = [json.dumps({"message": {"content": w + " "}, "done": False}) for w in text.split()]
                lines.append(json.dumps({"message": {"content": ""}, "done": True, "eval_count": 3}))
                return self._send(200, ("\n".join(lines) + "\n").encode(), "application/x-ndjson")
            return self._send(200, {"message": {"content": text}, "eval_count": 3})
        if self.path == "/v1/chat/completions":
            if self.headers.get("Authorization") != "Bearer good":
                return self._send(401, {"error": {"message": "Invalid API Key"}})
            if body.get("stream"):
                ev = [f"data: {json.dumps({'choices': [{'delta': {'content': w + ' '}}]})}\n\n" for w in text.split()]
                ev.append('data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":3}}\n\n')
                ev.append("data: [DONE]\n\n")
                return self._send(200, "".join(ev).encode(), "text/event-stream")
            return self._send(200, {"choices": [{"message": {"content": text}}], "usage": {"completion_tokens": 3}})
        self._send(404, {})


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def mk(tmp_path, server, key="good", **kw):
    return Settings(_env_file=None, data_dir=tmp_path / "data", vault_path=tmp_path / "vault", ollama_url=server,
                    fast_base_url=f"{server}/v1", fast_model="llama-3.3-70b-versatile", fast_api_key=key, **kw)


# ---------- configuration ----------
def test_env_comment_is_not_an_api_key(tmp_path):
    """`SYNAPSE_FAST_API_KEY=   # get a key at ...` used to send the comment to Groq as the key."""
    env = tmp_path / ".env"
    env.write_text("SYNAPSE_FAST_BASE_URL=https://api.groq.com/openai/v1\nSYNAPSE_FAST_MODEL=llama-3.3-70b-versatile\n"
                   "SYNAPSE_FAST_API_KEY=              # REQUIRED: get free key at https://console.groq.com/keys\n")
    s = Settings(_env_file=env)
    assert s.fast_api_key is None and not s.fast_enabled
    assert ModelRouter(s).current()["id"] == "ollama:qwen2.5:7b-instruct"


def test_real_key_enables_hosted_default(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SYNAPSE_FAST_BASE_URL=https://api.groq.com/openai/v1\nSYNAPSE_FAST_MODEL=llama-3.3-70b-versatile\n"
                   "SYNAPSE_FAST_API_KEY=gsk_abc123\n")
    s = Settings(_env_file=env, data_dir=tmp_path / "d")
    assert s.fast_enabled
    assert ModelRouter(s).current() == {"id": "groq:llama-3.3-70b-versatile", "provider": "groq",
                                        "model": "llama-3.3-70b-versatile", "local": False}


# ---------- providers ----------
async def test_streaming_providers_report_cumulative_text(server):
    for llm in (OllamaLLM(server, "qwen2.5:7b-instruct", 10), OpenAICompatLLM(f"{server}/v1", "good", "m", 10)):
        seen = []
        r = await llm.chat([{"role": "user", "content": "hi"}], on_token=seen.append)
        assert r.text.strip() == "hello streamed world"
        assert seen[0].strip() == "hello" and seen[-1].strip() == "hello streamed world"   # cumulative, growing
        assert r.output_tokens == 3


async def test_bad_key_falls_back_once_then_stays_local(server):
    fast, slow = OpenAICompatLLM(f"{server}/v1", "bad", "m", 10), OllamaLLM(server, "qwen2.5:7b-instruct", 10)
    llm = FallbackLLM(fast, slow)
    SEEN.clear()
    for _ in range(3):
        r = await llm.chat([{"role": "user", "content": "hi"}])
        assert r.provider == "ollama"
    hosted = [c for c in SEEN if c["path"] == "/v1/chat/completions"]
    assert len(hosted) == 1, "a rejected key must not be retried on every call"
    assert "401" in llm.last_error


# ---------- router ----------
async def test_router_lists_switches_and_persists(tmp_path, server):
    s = mk(tmp_path, server)
    r = ModelRouter(s)
    assert r.current()["id"] == "api:llama-3.3-70b-versatile"          # hosted is the default when configured
    ids = [m["id"] for m in await r.available()]
    assert "ollama:qwen2.5:7b-instruct" in ids and "api:llama-3.3-70b-versatile" in ids
    assert not any("embed" in i or "whisper" in i for i in ids)          # not chat models
    r.select("ollama:qwen2.5:7b-instruct")
    assert ModelRouter(s).current()["id"] == "ollama:qwen2.5:7b-instruct"   # survives a restart
    with pytest.raises(ValueError):
        r.select("openai:gpt-4o")                                        # not a configured provider
    SEEN.clear()
    resp = await r.chat([{"role": "user", "content": "x"}])
    assert resp.model == "qwen2.5:7b-instruct" and SEEN[-1]["path"] == "/api/chat"


async def test_run_keeps_its_model_when_switched_mid_run(tmp_path, server):
    """Pick a different model while a run is going: that run finishes on the model it started with."""
    s = mk(tmp_path, server, enable_web=False, enable_crew=False, enable_scheduler=False)
    async with Runtime(s) as rt:
        rt.llm.select("ollama:qwen2.5:7b-instruct")
        SEEN.clear()
        run_id = rt.start("hi")
        rt.llm.select("api:llama-3.3-70b-versatile")                   # user switches right after sending
        await rt._tasks[run_id]
        models = {c["model"] for c in SEEN if c["path"] in ("/api/chat", "/v1/chat/completions")}
        assert models == {"qwen2.5:7b-instruct"}, models
        d = await rt.details(run_id)
        assert d["stats"]["model"] == "qwen2.5:7b-instruct"


# ---------- dates ----------
async def test_every_prompt_knows_today(settings):
    """The 7B model wrote 'Due Date: 2024-03-01' in 2026 because only the planner knew the date."""
    llm = ScriptedLLM([{"mode": "task"}, plan([gen("s1", "List topics"), gen("s2", "Polish {{s1}}")]),
                       "topic list " * 5, "polished " * 5])
    async with Runtime(settings, llm) as rt:
        d = await rt.run("Suggest AI topics")
    assert d["status"] == "success"
    year = str(datetime.now().year)
    for msgs in llm.calls:                                           # triage, planner, both writer calls
        system = " ".join(m["content"] for m in msgs if m["role"] == "system")
        assert "Current date and time:" in system and year in system


class StreamingScripted(ScriptedLLM):
    """Scripted replies, delivered token by token like a real streaming model."""
    streams = True

    async def chat(self, messages, json_schema=None, max_tokens=None, on_token=None):
        r = await super().chat(messages, json_schema, max_tokens)
        if on_token:
            words = r.text.split(" ")
            for i in range(1, len(words) + 1):
                on_token(" ".join(words[:i]))
                await asyncio.sleep(0.03)
        return r


async def test_writer_text_reaches_the_dashboard_while_being_written(settings):
    text = " ".join(f"w{i}" for i in range(40))
    llm = StreamingScripted([{"mode": "task"}, plan([gen("s1", "Write it")]), text])
    async with Runtime(settings, llm) as rt:
        q = rt.subscribe("*")
        d = await rt.run("Suggest AI topics")
        partials = []
        while not q.empty():
            m = q.get_nowait()
            if m.get("type") == "progress" and m.get("partial"):
                partials.append(m)
    assert d["status"] == "success" and d["answer"] == text
    assert len(partials) >= 2, "the text should arrive in pieces, not only at the end"
    assert all(p["final"] and p["actor"] == "Writer" for p in partials)
    assert len(partials[-1]["partial"]) > len(partials[0]["partial"])


def test_streamer_throttles(monkeypatch):
    from synapse.graph import streamer
    sent = []
    cb = streamer(lambda st, **kw: sent.append(kw["partial"]), {}, every=0.05)
    for i in range(50):
        cb("x" * i)
    time.sleep(0.06)
    cb("final")
    assert 1 <= len(sent) <= 3 and sent[-1] == "final"


def test_retry_after_parsing():
    from synapse.llm import _retry_after
    assert _retry_after({"retry-after": "7"}) == 7
    assert _retry_after({"x-ratelimit-reset-tokens": "25.567s"}) == 25.567
    assert _retry_after({"x-ratelimit-reset-tokens": "1m26.4s"}) == 86.4
    assert _retry_after({"x-ratelimit-reset-tokens": "577ms"}) == 0.577
    assert _retry_after({}) is None
    groq = '{"error":{"message":"Rate limit reached ... Please try again in 4.7025s. Need more tokens?"}}'
    assert _retry_after({"x-ratelimit-reset-tokens": "40s"}, groq) == 4.7025    # the body's wait beats the bucket reset


async def test_short_rate_limit_waits_for_hosted_model_instead_of_falling_back():
    from synapse.llm import LLMResponse

    class Limited:
        calls = 0

        async def chat(self, *a, **k):
            Limited.calls += 1
            if Limited.calls == 1:
                raise LLMError("groq HTTP 429: rate limit", 429, retry_after=0.2)
            return LLMResponse(text="fast", provider="groq", model="m", latency_ms=1)

    class Local:
        async def chat(self, *a, **k):
            raise AssertionError("should not fall back for a short wait")

    r = await FallbackLLM(Limited(), Local()).chat([{"role": "user", "content": "x"}])
    assert r.text == "fast" and Limited.calls == 2


def test_tidy_answer_drops_invented_links_and_tracker_noise():
    from synapse.graph import tidy_answer
    real = "https://python.langchain.com/docs/introduction/"
    text = ("## Topics\n- [ ] **RAG** — see [LangChain docs](" + real + ")\n  - **Due Date:** 2024-03-01\n"
            "  - **Priority:** High\n- **Agents** — [guide](https://made-up.example.com/agents) https://fake.io/x\n")
    out = tidy_answer(text, "Suggest AI topics", {real})
    assert "[LangChain docs](" + real + ")" in out                  # fetched this run: kept
    assert "made-up.example.com" not in out and "fake.io" not in out and "[guide]" not in out
    assert "guide" in out                                           # link text survives
    assert "Due Date" not in out and "Priority" not in out and "[ ]" not in out
    # when the user asks for a schedule, dates and boxes stay
    kept = tidy_answer("- [ ] Week 1\n  - **Due Date:** 2026-10-04", "make a study plan with due dates", set())
    assert "Due Date" in kept and "[ ]" in kept


async def test_second_create_on_same_subject_appends_instead_of_duplicating(settings):
    """'Sid loves coffee' after 'make a note on Sid's personality' used to create a second note."""
    from tests.conftest import tool
    (settings.vault_path / "People").mkdir(parents=True)
    (settings.vault_path / "People/Sid's Personality.md").write_text("# Sid's Personality\n- calm\n")
    llm = ScriptedLLM([{"mode": "task"},
                       plan([tool("s1", "create_note", path="Sid's personality.md", content="- loves coffee, plays football")]),
                       "Added."])
    async with Runtime(settings, llm) as rt:
        d = await rt.run("He loves coffee and plays football, add it to Sid's personality note")
    notes = sorted(p.name for p in settings.vault_path.rglob("*.md"))
    assert notes == ["Sid's Personality.md"], notes
    body = (settings.vault_path / "People/Sid's Personality.md").read_text()
    assert "- calm" in body and "loves coffee" in body
    step = next(s for s in d["spans"] if s["name"] == "step")
    assert "appended to People/Sid's Personality.md" in step["attrs"]["redirected"]
    # the planner was shown the existing note
    assert "People/Sid's Personality.md" in llm.calls[1][-1]["content"]


async def test_dashboard_delete_moves_note_to_trash(settings):
    import httpx
    from asgi_lifespan import LifespanManager

    import synapse.api as api_mod
    settings.vault_path.mkdir(parents=True)
    (settings.vault_path / "Old.md").write_text("bye")
    orig = api_mod.Runtime
    api_mod.Runtime = type("R", (orig,), {"__init__": lambda self, s, llm=None: orig.__init__(self, s, ScriptedLLM([]))})
    try:
        app = api_mod.create_app(settings)
        async with LifespanManager(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.delete("/api/vault/note", params={"path": "Old.md"})
                assert r.status_code == 200 and r.json()["trash_path"].startswith(".trash/")
                assert (await c.delete("/api/vault/note", params={"path": "../etc/passwd"})).status_code == 404
    finally:
        api_mod.Runtime = orig
    assert not (settings.vault_path / "Old.md").exists()
    assert any((settings.vault_path / ".trash").glob("*Old.md"))


async def test_delete_request_is_not_mistaken_for_a_save(settings):
    """'Delete vault containing Sid personality' failed: the word 'vault' made it count as a save request."""
    from synapse.graph import wants_save
    from tests.conftest import approve, tool
    assert not wants_save("Delete vault containing Sid personality.")
    assert wants_save("save this to my vault") and wants_save("put it in the Research folder")
    settings.vault_path.mkdir(parents=True)
    (settings.vault_path / "Sid's personality.md").write_text("x")
    llm = ScriptedLLM([{"mode": "task"}, plan([tool("s1", "delete_note", path="Sid's personality.md")]), "Deleted."])
    async with Runtime(settings, llm) as rt:
        d = await rt.run("Delete vault containing Sid personality.", approve(True))
    assert d["status"] == "success", d["answer"]
    assert not (settings.vault_path / "Sid's personality.md").exists()


def test_slash_commands_pick_a_crew():
    from synapse.graph import slash_crew
    assert slash_crew("/study LangGraph") == "study" and slash_crew("/notes what do I know about Sid") == "vault"
    assert slash_crew("/decide LangGraph or CrewAI") == "decide" and slash_crew("what is /study") is None


async def test_crew_step_runs_through_the_graph_and_reports_progress(settings, monkeypatch):
    """A crew's progress events carry their own 'crew' key; forwarding them used to raise TypeError,
    so no crew ever ran from a plan. This drives a real crew step with a stand-in crew runner."""
    import synapse.graph as graph_mod
    seen = {}

    async def fake_run_crew(s, kind, brief, sources, on_agent=None, spec=None, toolbox=None):
        seen.update(kind=kind, toolbox=toolbox is not None)
        on_agent({"agent": "Advocate", "status": "running", "crew": kind})       # same shape crew.py emits
        on_agent({"agent": "tool", "status": "tool", "tool": "web_search", "detail": "x"})
        on_agent({"agent": "Judge", "status": "done", "chars": 10})
        return {"report": "## Recommendation\nLearn LangGraph first.", "crew": kind, "agents": ["Advocate", "Skeptic", "Judge"],
                "tools_used": ["web_search"], "tokens": 100}

    monkeypatch.setattr(graph_mod, "run_crew", fake_run_crew)
    llm = ScriptedLLM([plan([{"id": "s1", "kind": "crew", "crew": "decide", "description": "weigh it", "prompt": "LangGraph or CrewAI"}])])
    async with Runtime(settings, llm) as rt:
        q = rt.subscribe("*")
        d = await rt.run("/decide LangGraph or CrewAI first?")
        events = [q.get_nowait() for _ in range(q.qsize())]
    assert d["status"] == "success", d["answer"]
    assert "Learn LangGraph first" in d["answer"] and seen == {"kind": "decide", "toolbox": True}
    crew_live = [e for e in events if e.get("type") == "progress" and e.get("stage") == "crew"]
    assert crew_live and all(e["crew"] == "decide" for e in crew_live)
    step = next(s for s in d["spans"] if s["name"] == "step")
    assert step["attrs"]["crew"] == "decide" and step["attrs"]["crew_tools"] == ["web_search"]
    assert "s1" in d["tainted"]                      # used the web, so its output is untrusted
    triage = next(s for s in d["spans"] if s["name"] == "triage")
    assert triage["attrs"].get("crew") == "decide" and not triage["attrs"].get("llm")   # no classification call


def test_health_flags_code_edited_after_start(tmp_path, monkeypatch):
    import synapse.api as api_mod
    monkeypatch.setattr(api_mod, "STARTED", 0.0)                   # "started" long ago: every file counts as newer
    assert "graph.py" in api_mod.code_changed_since_start()
    monkeypatch.setattr(api_mod, "STARTED", 4e9)                   # started in the future: nothing newer
    assert api_mod.code_changed_since_start() == []


def test_crew_tools_correct_bad_args_cache_repeats_and_map_to_mcp():
    from synapse.crew import Budget, make_tools
    calls = []
    b = Budget(3)
    web, _page = make_tools(["search_the_web", "read_web_page"],
                            lambda name, args: calls.append((name, args)) or {"ok": True, "data": {"results": ["r"]}, "error": None}, b)
    assert "needs a 'query'" in web._run(cursor=2, id=0) and calls == []      # gpt-oss browser-style call
    first = web._run(query="langgraph")
    assert calls == [("web_search", {"query": "langgraph", "max_results": 6})] and "<untrusted_content" in first
    assert "already made this exact call" in web._run(query="langgraph") and len(calls) == 1
    assert b.used == ["web_search"]                                          # MCP name, so taint tracking sees it


def test_gmail_query_repair_and_reply_tool_is_guarded():
    from synapse import guardrails as G
    from synapse.mcp_servers.google import gmail_query
    assert gmail_query("to:v@gmail.com subject:Re: Hi Lanjodka") == 'to:v@gmail.com subject:"Hi Lanjodka"'
    assert gmail_query('subject:"Fwd: Budget plan" newer_than:7d') == 'subject:"Budget plan" newer_than:7d'
    assert gmail_query("from:x@y.com") == "from:x@y.com"
    assert G.assess_risk("gmail_read_thread", {}) == "low" and "gmail_read_thread" in G.READ_ONLY
    assert "gmail_read_thread" in G.UNTRUSTED_TOOLS          # other people's mail is untrusted input


async def test_gpt_oss_thinks_briefly_to_stay_inside_the_free_tier(server):
    SEEN.clear()
    await OpenAICompatLLM(f"{server}/v1", "good", "openai/gpt-oss-120b", 10, reasoning_effort="low").chat(
        [{"role": "user", "content": "x"}])
    await OpenAICompatLLM(f"{server}/v1", "good", "llama-3.3-70b-versatile", 10, reasoning_effort="low").chat(
        [{"role": "user", "content": "x"}])
    assert [x["reasoning_effort"] for x in SEEN] == ["low", None]


async def test_rate_limit_wait_of_half_a_minute_still_beats_local_planning(monkeypatch):
    from synapse.llm import LLMResponse
    seen = []

    class Limited:
        n = 0

        async def chat(self, *a, **k):
            Limited.n += 1
            if Limited.n == 1:
                raise LLMError("groq HTTP 429", 429, retry_after=31.6)
            return LLMResponse(text="fast", provider="groq", model="m", latency_ms=1)

    class Local:
        async def chat(self, *a, **k):
            raise AssertionError("fell back instead of waiting")

    import synapse.llm as L

    async def fake_sleep(sec):
        seen.append(sec)
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)
    r = await FallbackLLM(Limited(), Local()).chat([{"role": "user", "content": "x"}])
    assert r.text == "fast" and seen and seen[0] > 31
