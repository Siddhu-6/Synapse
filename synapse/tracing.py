"""Run + span store (SQLite). Span fields follow OpenTelemetry naming (trace_id/span_id/parent,
gen_ai.* attributes) so they can be exported to an OTLP backend such as Langfuse later."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

from .guardrails import redact


class TraceStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, trace_id TEXT, goal TEXT, status TEXT,
            created_at REAL, updated_at REAL, answer TEXT, stats TEXT, pending TEXT, conversation_id TEXT);
        CREATE INDEX IF NOT EXISTS idx_runs_conv ON runs(conversation_id);
        CREATE TABLE IF NOT EXISTS spans(run_id TEXT, seq INTEGER, span_id TEXT, name TEXT, ts REAL,
            latency_ms INTEGER, attrs TEXT, PRIMARY KEY(run_id, seq));
        """)

    def create_run(self, run_id: str, goal: str, conversation_id: str = "") -> dict:
        now = time.time()
        self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (run_id, secrets.token_hex(16), redact(goal), "running", now, now, None, "{}", None,
                         conversation_id or run_id))
        self.db.commit()
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields) -> None:
        for k in ("stats", "pending"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], default=str)
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        self.db.commit()

    def add_span(self, run_id: str, event: dict) -> dict:
        seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM spans WHERE run_id=?", (run_id,)).fetchone()[0]
        span = {"seq": seq, "span_id": secrets.token_hex(8), "name": event.get("node", "event"),
                "ts": event.get("ts", time.time()), "latency_ms": event.get("latency_ms", 0),
                "attrs": {k: v for k, v in event.items() if k not in ("node", "ts", "latency_ms")}}
        self.db.execute("INSERT INTO spans VALUES(?,?,?,?,?,?,?)", (run_id, seq, span["span_id"], span["name"], span["ts"],
                        span["latency_ms"], redact(json.dumps(span["attrs"], default=str))))
        self.db.commit()
        return span

    def get_run(self, run_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["stats"] = json.loads(d["stats"] or "{}")
        d["pending"] = json.loads(d["pending"]) if d["pending"] else None
        return d

    def spans(self, run_id: str) -> list[dict]:
        return [dict(r) | {"attrs": json.loads(r["attrs"])} for r in
                self.db.execute("SELECT * FROM spans WHERE run_id=? ORDER BY seq", (run_id,))]

    def list_runs(self, limit: int = 50, conversation_id: str | None = None) -> list[dict]:
        sql = ("SELECT id,goal,status,created_at,updated_at,stats,pending,conversation_id,answer FROM runs"
               + (" WHERE conversation_id=?" if conversation_id else "")
               + " ORDER BY created_at DESC LIMIT ?")
        args = (conversation_id, limit) if conversation_id else (limit,)
        return [dict(r) | {"stats": json.loads(r["stats"] or "{}"), "pending": bool(r["pending"])}
                for r in self.db.execute(sql, args)]

    def conversations(self, limit: int = 40) -> list[dict]:
        """One row per chat: its first goal as the title, plus counts and latest activity."""
        rows = self.db.execute(
            """SELECT conversation_id AS id, COUNT(*) AS turns, MAX(updated_at) AS updated_at,
                      MIN(created_at) AS created_at,
                      SUM(CASE WHEN pending IS NOT NULL THEN 1 ELSE 0 END) AS pending
               FROM runs GROUP BY conversation_id ORDER BY MAX(updated_at) DESC LIMIT ?""", (limit,)).fetchall()
        out = []
        for r in rows:
            goals = [g["goal"] for g in self.db.execute(
                "SELECT goal FROM runs WHERE conversation_id=? ORDER BY created_at LIMIT 5", (r["id"],))]
            # title the chat by its first real request, not an opening "hi"
            title = next((g for g in goals if len(g.split()) >= 3), goals[0] if goals else "(empty)")
            out.append(dict(r) | {"title": title, "pending": bool(r["pending"])})
        return out

    def history(self, conversation_id: str, limit: int = 6) -> list[dict]:
        """Previous turns of this chat, oldest first, for follow-up context ("the above message")."""
        rows = self.db.execute(
            """SELECT goal, answer, status FROM runs WHERE conversation_id=? AND answer IS NOT NULL
               ORDER BY created_at DESC LIMIT ?""", (conversation_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def delete_conversation(self, conversation_id: str) -> int:
        """Delete all runs and their spans for a conversation. Returns count of deleted runs."""
        runs = self.db.execute("SELECT id FROM runs WHERE conversation_id=?", (conversation_id,)).fetchall()
        if not runs:
            return 0
        ids = [r["id"] for r in runs]
        for rid in ids:
            self.db.execute("DELETE FROM spans WHERE run_id=?", (rid,))
        self.db.execute("DELETE FROM runs WHERE conversation_id=?", (conversation_id,))
        self.db.commit()
        return len(ids)

    def metrics(self) -> dict:
        rows = self.db.execute("SELECT status, stats FROM runs").fetchall()
        lat = sorted(json.loads(r["stats"] or "{}").get("latency_ms", 0) for r in rows if r["status"] in ("success", "failed"))
        by = {}
        for r in rows:
            by[r["status"]] = by.get(r["status"], 0) + 1
        pct = lambda p: lat[min(int(len(lat) * p), len(lat) - 1)] if lat else 0  # noqa: E731
        tools = self.db.execute("SELECT attrs FROM spans WHERE name='step'").fetchall()
        tool_calls = [json.loads(t["attrs"]).get("tool_call") for t in tools]
        tool_calls = [t for t in tool_calls if t]
        return {"runs": len(rows), "by_status": by, "runs_success": by.get("success", 0),
                "latency_p50_ms": pct(0.5), "latency_p95_ms": pct(0.95),
                "tool_calls": len(tool_calls), "tool_errors": sum(not t["ok"] for t in tool_calls)}
