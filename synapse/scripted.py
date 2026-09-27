"""Deterministic LLM stand-in used by tests and offline evals.

It replaces ONLY the model. Graph, MCP servers, guardrails, checkpointer, memory and the vault are real.
"""
from __future__ import annotations

import json

from .llm import LLMResponse


class ScriptedLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, messages, json_schema=None, max_tokens=None):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        r = self.replies.pop(0)
        return LLMResponse(text=r if isinstance(r, str) else json.dumps(r), provider="scripted", model="scripted",
                           latency_ms=1, input_tokens=10, output_tokens=5)


def plan(steps, **kw) -> dict:
    return {"intent": kw.get("intent", "test"), "deliverable": kw.get("deliverable", "x"),
            "reasoning": kw.get("reasoning", "r"), "steps": steps, "direct_answer": kw.get("direct_answer")}


def gen(sid: str, prompt: str = "write") -> dict:
    return {"id": sid, "kind": "generate", "description": f"generate {sid}", "prompt": prompt}


def tool(sid: str, name: str, **args) -> dict:
    return {"id": sid, "kind": "tool", "description": f"{name} {sid}", "tool": name, "args": args}


def approver(ok: bool = True, comment: str = "ok"):
    async def f(pending):
        f.seen.append(pending)
        return {"approved": ok, "comment": comment}
    f.seen = []
    return f
