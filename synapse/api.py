"""HTTP API + SSE stream for the dashboard.

Auth: if SYNAPSE_API_TOKEN is set, every /api route requires `Authorization: Bearer <token>`.
Single-user by design; the token is the whole authorisation model and is documented as such.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .runtime import Runtime

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
STATIC = Path(__file__).parent / "static"
STREAM_MAX = 45.0
CODE = Path(__file__).parent
STARTED = time.time()


def code_changed_since_start() -> list[str]:
    """Python files edited after this server started: the running process still has the OLD code
    (it only reads them at startup), which looked exactly like "the fix didn't work"."""
    return sorted(p.relative_to(CODE).as_posix() for p in CODE.rglob("*.py") if p.stat().st_mtime > STARTED + 1)


def ui_build() -> str | None:
    try:
        m = re.search(r"assets/(index-[\w-]+\.js)", (STATIC / "index.html").read_text())
        return m.group(1) if m else None
    except OSError:
        return None


class GoalIn(BaseModel):
    goal: str = Field(min_length=1, max_length=20000)
    conversation_id: str = Field(default="", max_length=64)


class MessageIn(BaseModel):
    source: str = Field(default="whatsapp", max_length=32)
    sender: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)


class ScheduleIn(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    cadence: str = "once"
    at: str = ""


class ModelIn(BaseModel):
    id: str = Field(min_length=3, max_length=200)


class Decision(BaseModel):
    approved: bool
    comment: str = ""


def vault_graph(vault: Path, limit: int = 400) -> dict:
    """Obsidian-style graph from wikilinks. Read-only, computed straight from the markdown files."""
    notes, edges = {}, []
    for p in sorted(vault.rglob("*.md"))[:limit]:
        if any(part.startswith(".") for part in p.relative_to(vault).parts):
            continue
        rel = p.relative_to(vault).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        notes[p.stem.lower()] = rel
        notes.setdefault(rel.lower(), rel)
        stat = p.stat()
        edges.append((rel, [m.group(1).strip() for m in WIKILINK.finditer(text)],
                      {"path": rel, "folder": p.parent.relative_to(vault).as_posix() or "/",
                       "size": stat.st_size, "mtime": stat.st_mtime,
                       "synapse": "created_by: synapse" in text[:400]}))
    nodes = {e[2]["path"]: e[2] | {"id": e[2]["path"], "links": 0} for e in edges}
    links = []
    for src, targets, _ in edges:
        for t in targets:
            dst = notes.get(t.lower()) or notes.get(t.lower() + ".md")
            if dst and dst in nodes and dst != src:
                links.append({"source": src, "target": dst})
                nodes[src]["links"] += 1
                nodes[dst]["links"] += 1
    return {"nodes": list(nodes.values()), "links": links}


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    state: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with Runtime(s) as rt:
            state["rt"] = rt
            yield

    app = FastAPI(title="Synapse API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

    def auth(request: Request) -> None:
        if s.api_token is None:
            return
        tok = s.api_token.get_secret_value()
        header = request.headers.get("authorization") or ""
        query = request.query_params.get("token") or ""
        # constant-time comparisons, so the token can't be guessed from response timing
        if not (hmac.compare_digest(header, f"Bearer {tok}") or hmac.compare_digest(query, tok)):
            raise HTTPException(401, "invalid or missing API token")

    def rt() -> Runtime:
        if "rt" not in state:
            raise HTTPException(503, "runtime not ready")
        return state["rt"]

    api = {"dependencies": [Depends(auth)]}

    @app.get("/api/health")
    async def health():
        r = rt()
        cur = r.llm.current() if hasattr(r.llm, "current") else {"id": s.model, "provider": s.llm_provider, "model": s.model, "local": True}
        return {"ok": True, "provider": cur["provider"], "model": cur["model"], "model_id": cur["id"], "local": cur["local"],
                "fallback": getattr(r.llm, "local_id", None) and not cur["local"],
                "tools": len(r.tools.specs),
                "servers": r.tools.servers, "notes": len(list(s.vault_path.rglob("*.md"))),
                "fast": s.fast_enabled,
                "vault": str(s.vault_path.resolve()), "crew": s.enable_crew, "web": s.enable_web,
                "restart_needed": code_changed_since_start(), "ui_build": ui_build()}

    @app.get("/api/metrics", **api)
    async def metrics():
        r = rt()
        m = r.traces.metrics()
        mem = r.memory.dump()
        return {**m, "facts": len(mem["facts"]), "episodes": len(mem["episodes"]), "ts": time.time()}

    @app.get("/api/models", **api)
    async def models():
        """Chat models Synapse can use right now, and which one is selected."""
        r = rt()
        if not hasattr(r.llm, "available"):
            return {"current": {"id": s.model, "provider": s.llm_provider, "model": s.model, "local": True}, "models": []}
        return {"current": r.llm.current(), "models": await r.llm.available()}

    @app.post("/api/models", **api)
    async def select_model(body: ModelIn):
        r = rt()
        if not hasattr(r.llm, "select"):
            raise HTTPException(409, "model switching is not available")
        try:
            return {"current": r.llm.select(body.id)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/tools", **api)
    async def tools():
        return {"tools": rt().tools.catalog()}

    @app.post("/api/runs", **api)
    async def start_run(body: GoalIn):
        return {"run_id": rt().start(body.goal.strip(), body.conversation_id.strip())}

    @app.get("/api/runs", **api)
    async def list_runs(limit: int = 50, conversation_id: str | None = None):
        return {"runs": rt().traces.list_runs(limit, conversation_id)}

    @app.get("/api/conversations", **api)
    async def conversations(limit: int = 40):
        return {"conversations": rt().traces.conversations(limit)}

    @app.get("/api/runs/{run_id}", **api)
    async def get_run(run_id: str):
        d = await rt().details(run_id)
        if not d:
            raise HTTPException(404, "unknown run")
        return d

    @app.post("/api/runs/{run_id}/approve", **api)
    async def approve(run_id: str, d: Decision):
        try:
            rt().resume(run_id, d.approved, d.comment)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @app.get("/api/stream", **api)
    async def stream(request: Request, run_id: str = "*"):
        r = rt()
        q = r.subscribe(run_id)

        async def gen():
            # Each stream lives at most STREAM_MAX seconds, then the browser reconnects on its own (and the
            # dashboard re-syncs). Browsers allow ~6 connections per host; permanent streams from several open
            # tabs used to take them all, so a new tab at :8000 never finished loading.
            yield "retry: 1500\n: connected\n\n"
            ends = time.monotonic() + STREAM_MAX
            try:
                while time.monotonic() < ends:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=min(15, max(ends - time.monotonic(), 0.1)))
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(msg, default=str)}\n\n"
            finally:
                r.unsubscribe(run_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/messages", **api)
    async def inbound_message(m: MessageIn):
        """Wire n8n's WhatsApp/SMS trigger here. Synapse replies to routine messages and pings you
        for anything that needs a person. Requires the API token when one is set."""
        return {"run_id": rt().on_message(m.source, m.sender, m.text)}

    @app.get("/api/schedules", **api)
    async def schedules():
        return {"schedules": rt().scheduler.list(include_inactive=True)}

    @app.post("/api/schedules", **api)
    async def add_schedule(body: ScheduleIn):
        try:
            return rt().scheduler.add(body.goal, body.cadence, body.at)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/schedules/{schedule_id}", **api)
    async def cancel_schedule(schedule_id: str):
        if not rt().scheduler.cancel(schedule_id):
            raise HTTPException(404, "no active schedule with that id")
        return {"ok": True}

    @app.delete("/api/conversations/{conversation_id}", **api)
    async def delete_conversation(conversation_id: str):
        n = rt().traces.delete_conversation(conversation_id)
        if not n:
            raise HTTPException(404, "no conversation with that id")
        return {"ok": True, "deleted_runs": n}

    @app.get("/api/memory", **api)
    async def memory():
        return rt().memory.dump()

    @app.delete("/api/memory/{fact_id}", **api)
    async def forget(fact_id: int):
        if not rt().memory.forget(fact_id):
            raise HTTPException(404, "unknown fact")
        return {"ok": True}

    @app.get("/api/vault/graph", **api)
    async def graph():
        return vault_graph(s.vault_path)

    @app.get("/api/vault/note", **api)
    async def note(path: str):
        p = (s.vault_path / path).resolve()
        if not p.is_relative_to(s.vault_path.resolve()) or not p.is_file():
            raise HTTPException(404, "note not found")
        return {"path": path, "content": p.read_text(encoding="utf-8", errors="ignore")[:200_000]}

    @app.delete("/api/vault/note", **api)
    async def delete_note(path: str):
        """Delete from the dashboard. Like the delete_note tool, the note moves to the vault's .trash/ (recoverable)."""
        root = s.vault_path.resolve()
        p = (root / path).resolve()
        if not p.is_relative_to(root) or not p.is_file() or p.suffix != ".md" or any(
                x.startswith(".") for x in p.relative_to(root).parts):
            raise HTTPException(404, "note not found")
        dest = root / ".trash" / f"{time.strftime('%Y%m%d%H%M%S')}_{p.name}"
        dest.parent.mkdir(exist_ok=True)
        p.replace(dest)
        return {"deleted": p.relative_to(root).as_posix(), "trash_path": dest.relative_to(root).as_posix()}

    @app.get("/api/evals/latest", **api)
    async def evals():
        f = s.data_dir / "evals" / "latest.json"
        if not f.is_file():
            raise HTTPException(404, "no eval report yet; run `python -m evals.run`")
        return json.loads(f.read_text())

    if STATIC.is_dir():  # built dashboard, served by the same process
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            f = STATIC / full_path
            return FileResponse(f if f.is_file() and f.suffix else STATIC / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn
    s = get_settings()
    uvicorn.run("synapse.api:app", host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
