"""Persistent memory (SQLite).

- Working memory: the LangGraph state of a run (checkpointed separately).
- Episodic memory: one record per finished run (goal, outcome, notes written) with provenance.
- Semantic memory: durable facts/preferences the USER stated, with provenance (run id, source).
Knowledge produced by research goes to the Obsidian vault, not here.
Embeddings come from Ollama when available; otherwise retrieval falls back to keyword overlap.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path

import httpx

WORD = re.compile(r"[a-z0-9]{3,}")


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _kw(a: str, b: str) -> float:
    A, B = set(WORD.findall(a.lower())), set(WORD.findall(b.lower()))
    return len(A & B) / math.sqrt(len(A) * len(B)) if A and B else 0.0


class Embedder:
    def __init__(self, url: str, model: str):
        self.url, self.model, self.available = url.rstrip("/"), model, True

    async def embed(self, text: str) -> list[float] | None:
        if not self.available:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(f"{self.url}/api/embed", json={"model": self.model, "input": text[:4000]})
            if r.status_code != 200:
                self.available = False  # model not pulled: degrade to keyword search, don't retry every call
                return None
            return r.json()["embeddings"][0]
        except (httpx.HTTPError, KeyError, IndexError):
            return None


class Memory:
    def __init__(self, path: Path, embedder: Embedder | None = None):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.emb = embedder
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, text TEXT NOT NULL, kind TEXT, source TEXT,
            run_id TEXT, created_at REAL, embedding TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY, run_id TEXT UNIQUE, goal TEXT, status TEXT,
            summary TEXT, artifacts TEXT, created_at REAL);
        """)

    async def add_fact(self, text: str, kind: str, run_id: str, source: str = "user_goal") -> dict:
        text = text.strip()
        vec = await self.emb.embed(text) if self.emb else None
        for r in self.db.execute("SELECT id, text, embedding FROM facts WHERE active=1"):
            same = r["text"].lower() == text.lower()
            if not same and vec and r["embedding"]:
                same = _cos(vec, json.loads(r["embedding"])) > 0.92
            if same:
                return {"stored": False, "duplicate_of": r["id"]}
        cur = self.db.execute("INSERT INTO facts(text,kind,source,run_id,created_at,embedding) VALUES(?,?,?,?,?,?)",
                              (text, kind, source, run_id, time.time(), json.dumps(vec) if vec else None))
        self.db.commit()
        return {"stored": True, "id": cur.lastrowid}

    async def recall(self, query: str, k: int = 5, min_score: float = 0.35) -> dict:
        qv = await self.emb.embed(query) if self.emb else None
        scored = []
        for r in self.db.execute("SELECT * FROM facts WHERE active=1"):
            if qv and r["embedding"]:
                s = _cos(qv, json.loads(r["embedding"]))
            else:
                s = _kw(query, r["text"]) + 0.2  # keyword fallback is on a different scale
            if s >= min_score:
                scored.append((s, {"id": r["id"], "text": r["text"], "kind": r["kind"], "run_id": r["run_id"], "score": round(s, 3)}))
        scored.sort(key=lambda x: -x[0])
        eps = [(_kw(query, e["goal"]), dict(e)) for e in self.db.execute(
            "SELECT run_id, goal, status, summary, artifacts, created_at FROM episodes ORDER BY created_at DESC LIMIT 200")]
        eps = [e for s, e in sorted(eps, key=lambda x: -x[0]) if s > 0.25][:3]
        return {"facts": [f for _, f in scored[:k]], "episodes": eps}

    def profile(self, limit: int = 12) -> list[dict]:
        """Who the user is, returned on EVERY run. recall() only returns facts similar to the goal, so a
        request like "send it to him" never brought back the user's name, and drafts were signed
        "[Your Name]"."""
        return [dict(r) for r in self.db.execute(
            "SELECT id, text, kind, run_id FROM facts WHERE active=1 AND kind IN ('profile','name','contact') "
            "ORDER BY created_at LIMIT ?", (limit,))]

    def said(self, limit: int = 200) -> list[str]:
        """The user's own past requests, newest first — where "my name is ..." was actually said."""
        return [r["goal"] for r in self.db.execute(
            "SELECT goal FROM episodes ORDER BY created_at DESC LIMIT ?", (limit,))]

    def add_episode(self, run_id: str, goal: str, status: str, summary: str, artifacts: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO episodes(run_id,goal,status,summary,artifacts,created_at) VALUES(?,?,?,?,?,?)",
                        (run_id, goal, status, summary[:2000], json.dumps(artifacts), time.time()))
        self.db.commit()

    def forget(self, fact_id: int) -> bool:
        cur = self.db.execute("UPDATE facts SET active=0 WHERE id=?", (fact_id,))
        self.db.commit()
        return cur.rowcount > 0

    def dump(self) -> dict:
        return {"facts": [dict(r) | {"embedding": bool(r["embedding"])} for r in
                          self.db.execute("SELECT * FROM facts WHERE active=1 ORDER BY created_at DESC")],
                "episodes": [dict(r) for r in self.db.execute("SELECT * FROM episodes ORDER BY created_at DESC LIMIT 100")]}
