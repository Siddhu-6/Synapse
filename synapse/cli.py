"""Usage:
  python -m synapse.cli doctor
  python -m synapse.cli run "your goal"      (risky actions prompt for approval in the terminal)
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

from .config import get_settings
from .runtime import Runtime


async def _approve(p: dict) -> dict:
    print(f"\n[APPROVAL] {p['description']}\n  tool={p['tool']} risk={p['risk']} tainted={p['tainted']}")
    print("  args=" + json.dumps(p["args"], indent=2)[:1500])
    ans = await asyncio.to_thread(input, "Approve? [y/N] ")
    return {"approved": ans.strip().lower() in ("y", "yes"), "comment": "cli"}


async def run(goal: str) -> None:
    async with Runtime(get_settings()) as rt:
        d = await rt.run(goal, approver=_approve)
    for sp in d["spans"]:
        a = sp["attrs"]
        toks = sum((c.get("output_tokens") or 0) for c in a.get("llm", []))
        label = a.get("tool") or a.get("kind") or a.get("status") or ""
        print(f"  {sp['name']:<9} {sp['latency_ms']:>7}ms  out_tok={toks:<5} {a.get('step', '')} {label} {a.get('error', '')}")
    print(f"\nstatus: {d['status']}  run: {d['id']}  stats: {json.dumps(d['stats'])}\n\n{d['answer']}")


async def doctor() -> int:
    s = get_settings()
    ok = True
    print(f"provider={s.llm_provider} model={s.model} vault={s.vault_path.resolve()} data={s.data_dir.resolve()}")
    if s.llm_provider == "ollama":
        try:
            names = [m["name"] for m in httpx.get(f"{s.ollama_url}/api/tags", timeout=5).json().get("models", [])]
            for m, required in ((s.model, True), (s.embed_model, False)):
                has = any(n == m or n.split(":")[0] == m for n in names)
                print(f"[{'ok' if has else ('FAIL' if required else 'warn')}] model {m}: {'present' if has else 'missing -> ollama pull ' + m}")
                ok &= has or not required
        except httpx.HTTPError as e:
            print(f"[FAIL] ollama not reachable at {s.ollama_url}: {e}")
            ok = False
    from .tools import Toolbox
    async with Toolbox(s) as tb:
        for name, st in tb.servers.items():
            print(f"[{'ok' if st == 'up' else 'FAIL'}] MCP server {name}: {st}")
            ok &= st == "up"
        print(f"      tools: {sorted(tb.specs)}")
    print(f"[{'ok' if s.google_token_path.exists() else 'info'}] Google: {'connected' if s.google_token_path.exists() else 'not connected (optional)'}")
    return 0 if ok else 1


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "doctor":
        sys.exit(asyncio.run(doctor()))
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        print(__doc__)
        sys.exit(2)
    asyncio.run(run(" ".join(sys.argv[2:])))


if __name__ == "__main__":
    main()
