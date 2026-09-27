import json

import pytest

from synapse.config import Settings
from synapse.llm import LLMResponse


class ScriptedLLM:
    """Test double for the LLM ONLY. Graph, MCP servers, checkpointer, memory and vault are all real."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, messages, json_schema=None, max_tokens=None):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        r = self.replies.pop(0)
        return LLMResponse(text=r if isinstance(r, str) else json.dumps(r), provider="scripted", model="scripted",
                           latency_ms=1, input_tokens=10, output_tokens=5)


@pytest.fixture
def settings(tmp_path):
    return Settings(vault_path=tmp_path / "vault", data_dir=tmp_path / "data", max_replans=1, enable_web=False,
                    ollama_url="http://127.0.0.1:9", _env_file=None)


def plan(steps, **kw):
    return {"intent": kw.get("intent", "test"), "deliverable": "x", "reasoning": "r", "steps": steps,
            "direct_answer": kw.get("direct_answer")}


def gen(sid, prompt="write"):
    return {"id": sid, "kind": "generate", "description": f"generate {sid}", "prompt": prompt}


def tool(sid, name, **args):
    return {"id": sid, "kind": "tool", "description": f"{name} {sid}", "tool": name, "args": args}


def approve(ok=True):
    async def f(pending):
        f.seen.append(pending)
        return {"approved": ok, "comment": "test"}
    f.seen = []
    return f
