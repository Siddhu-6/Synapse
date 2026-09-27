"""Run the eval suite.

  python -m evals.run              # offline, deterministic (scripted model, real everything else)
  python -m evals.run --live       # same goals against the configured LLM, structural checks only

Writes data/evals/latest.json, which the dashboard reads.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from synapse.config import Settings, get_settings
from synapse.runtime import Runtime
from synapse.scripted import ScriptedLLM, approver

from .scenarios import SCENARIOS, Scenario


async def run_scenario(sc: Scenario) -> dict:
    t0 = time.perf_counter()
    if "static" in sc.tags:
        checks = [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in
                  ((n, *f({}, {})) for n, f in sc.checks)]
        return {"id": sc.id, "category": sc.category, "goal": sc.goal, "status": "n/a",
                "checks": checks, "passed": all(c["passed"] for c in checks),
                "duration_ms": int((time.perf_counter() - t0) * 1000)}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        s = Settings(vault_path=root / "vault", data_dir=root / "data", enable_web=False,
                     ollama_url="http://127.0.0.1:9", _env_file=None)
        s.ensure_dirs()
        if sc.setup:
            sc.setup(s.vault_path)
        appr = approver(sc.approve) if sc.approve is not None else None
        llm = ScriptedLLM(sc.replies if "chat" in sc.tags else [{"mode": "task"}, *sc.replies])
        try:
            async with Runtime(s, llm) as rt:
                res = await rt.run(sc.goal, appr)
                ctx = {"settings": s, "approver": appr, "runtime": rt, "llm": llm}
                checks = []
                for name, fn in sc.checks:
                    try:
                        ok, detail = fn(res, ctx)
                    except Exception as e:  # a check that explodes is a failed check, not a crashed suite
                        ok, detail = False, f"check error: {type(e).__name__}: {e}"
                    checks.append({"name": name, "passed": bool(ok), "detail": "" if ok else detail})
        except Exception as e:
            return {"id": sc.id, "category": sc.category, "goal": sc.goal, "status": "error",
                    "error": f"{type(e).__name__}: {e}", "checks": [], "passed": False,
                    "duration_ms": int((time.perf_counter() - t0) * 1000)}
    return {"id": sc.id, "category": sc.category, "goal": sc.goal, "status": res["status"],
            "answer": (res.get("answer") or "")[:400], "stats": res.get("stats", {}),
            "checks": checks, "passed": all(c["passed"] for c in checks),
            "duration_ms": int((time.perf_counter() - t0) * 1000)}


async def run_live(goals: list[str]) -> list[dict]:
    """Smoke-test the real model: does it plan validly, pick sane tools and finish?"""
    s = get_settings()
    out = []
    async with Runtime(s) as rt:
        for g in goals:
            t0 = time.perf_counter()
            res = await rt.run(g, approver(True))
            steps = (res.get("plan") or {}).get("steps", [])
            checks = [
                {"name": "produced a valid plan or direct answer", "passed": bool(steps or res.get("answer")), "detail": ""},
                {"name": "every planned tool exists", "passed": all(st.get("tool") in rt.tools.specs
                                                                    for st in steps if st["kind"] == "tool"), "detail": ""},
                {"name": "run finished successfully", "passed": res["status"] == "success", "detail": res["status"]},
            ]
            out.append({"id": g[:40], "category": "live", "goal": g, "status": res["status"],
                        "answer": (res.get("answer") or "")[:400], "stats": res.get("stats", {}),
                        "checks": checks, "passed": all(c["passed"] for c in checks),
                        "duration_ms": int((time.perf_counter() - t0) * 1000)})
    return out


LIVE_GOALS = [
    "What is the Model Context Protocol? Answer in two sentences.",
    "Write a short note about vector databases and save it as Research/Vector DBs.md",
    "Search my notes for anything about vector databases",
]


async def main_async(live: bool, only: str | None) -> int:
    t0 = time.perf_counter()
    if live:
        results = await run_live(LIVE_GOALS)
    else:
        scs = [s for s in SCENARIOS if not only or only in s.id or only in s.category]
        results = [await run_scenario(sc) for sc in scs]

    by_cat: dict[str, dict] = {}
    for r in results:
        c = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += r["passed"]
    report = {"ts": time.time(), "mode": "live" if live else "offline",
              "passed": sum(r["passed"] for r in results), "total": len(results),
              "duration_ms": int((time.perf_counter() - t0) * 1000),
              "by_category": by_cat, "results": results}

    out_dir = get_settings().data_dir / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2, default=str))

    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['category']:<22} {r['id']:<32} {r['duration_ms']:>6}ms")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"        - {c['name']}: {c['detail']}")
        if r.get("error"):
            print(f"        ! {r['error']}")
    print(f"\n{report['passed']}/{report['total']} scenarios passed in {report['duration_ms']}ms "
          f"-> {out_dir / 'latest.json'}")
    return 0 if report["passed"] == report["total"] else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="run against the configured LLM instead of scripted replies")
    ap.add_argument("--only", help="filter by scenario id or category")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(a.live, a.only)))


if __name__ == "__main__":
    main()
