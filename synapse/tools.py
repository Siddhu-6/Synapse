"""MCP client boundary: discovery, param validation, timeouts, retries, typed results."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

from .config import Settings
from .guardrails import READ_ONLY, RISK_POLICY, Risk


@dataclass
class ToolSpec:
    name: str
    server: str
    description: str
    input_schema: dict
    risk: Risk

    def catalog_entry(self) -> dict:
        """Compact form for the planner prompt: every token here is paid on every planning call."""
        schema = self.input_schema.get("properties", {})
        req = set(self.input_schema.get("required", []))
        params = {k: v.get("type", "any") + ("" if k in req else "?") for k, v in schema.items()}
        desc = self.description.strip().split("\n")[0]
        if len(desc) > 110:
            desc = desc[:107].rsplit(" ", 1)[0] + "..."
        return {"name": self.name, "params": params, "risk": self.risk, "description": desc}


class ToolResult(BaseModel):
    tool: str
    args: dict
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: int = 0
    attempts: int = 1


def server_configs(s: Settings) -> dict[str, StdioServerParameters]:
    env = {**os.environ, "SYNAPSE_VAULT_PATH": str(s.vault_path.expanduser().resolve()),
           "SYNAPSE_DATA_DIR": str(s.data_dir.expanduser().resolve())}
    mk = lambda mod: StdioServerParameters(command=sys.executable, args=["-m", f"synapse.mcp_servers.{mod}"], env=env)  # noqa: E731
    env["SYNAPSE_NOTION_TOKEN"] = s.notion_token.get_secret_value() if s.notion_token else ""
    env["SYNAPSE_N8N_WEBHOOKS"] = s.n8n_webhooks
    env["SYNAPSE_N8N_SECRET"] = s.n8n_secret.get_secret_value() if s.n8n_secret else ""
    cfg = {"vault": mk("vault"), "tasks": mk("tasks"), "automation": mk("automation"), "world": mk("world")}
    if s.notion_token:
        cfg["notion"] = mk("notion")
    if s.enable_web:
        cfg["research"] = mk("research")
    if s.google_token_path.exists():
        cfg["google"] = mk("google")
    return cfg


class Toolbox:
    def __init__(self, settings: Settings):
        self.s = settings
        self.specs: dict[str, ToolSpec] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._stack = AsyncExitStack()
        self.servers: dict[str, str] = {}  # name -> status

    async def __aenter__(self):
        errlog = open(os.devnull, "w")
        self._stack.callback(errlog.close)
        for name, params in server_configs(self.s).items():
            try:
                r, w = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
                sess = await self._stack.enter_async_context(ClientSession(r, w))
                await asyncio.wait_for(sess.initialize(), 20)
                for t in (await sess.list_tools()).tools:
                    self.specs[t.name] = ToolSpec(t.name, name, t.description or "", t.inputSchema,
                                                  RISK_POLICY.get(t.name, "high"))
                self._sessions[name] = sess
                self.servers[name] = "up"
            except Exception as e:  # one broken server must not take the assistant down
                self.servers[name] = f"down: {type(e).__name__}: {e}"
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    def catalog(self) -> list[dict]:
        return [s.catalog_entry() for s in self.specs.values()]

    async def call(self, name: str, args: dict) -> ToolResult:
        t0 = time.perf_counter()
        ms = lambda: int((time.perf_counter() - t0) * 1000)  # noqa: E731
        spec = self.specs.get(name)
        if spec is None:
            return ToolResult(tool=name, args=args, ok=False, error=f"unknown tool '{name}'")
        try:
            jsonschema.validate(args, spec.input_schema)
        except jsonschema.ValidationError as e:
            return ToolResult(tool=name, args=args, ok=False, error=f"invalid params: {e.message}")
        # Only read-only tools are retried; retrying a write after a timeout could apply it twice.
        max_attempts = 2 if name in READ_ONLY else 1
        err = ""
        for attempt in range(1, max_attempts + 1):
            try:
                res = await asyncio.wait_for(self._sessions[spec.server].call_tool(name, args), self.s.tool_timeout)
            except TimeoutError:
                err = f"timeout after {self.s.tool_timeout}s"
                continue
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                continue
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            if res.isError:
                return ToolResult(tool=name, args=args, ok=False, error=text.replace(f"Error executing tool {name}: ", "") or "tool error",
                                  latency_ms=ms(), attempts=attempt)
            data = res.structuredContent
            if data is None:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"text": text}
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"]
            return ToolResult(tool=name, args=args, ok=True, data=data, latency_ms=ms(), attempts=attempt)
        return ToolResult(tool=name, args=args, ok=False, error=err, latency_ms=ms(), attempts=max_attempts)
