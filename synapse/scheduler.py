"""Scheduler: run a goal later, or on a daily/weekly cadence.

Kept deliberately simple — SQLite plus one polling loop. No cron parser, no external scheduler: the
supported shapes are "once at a time" and "every day/weekday/week at HH:MM", which covers what a
personal assistant actually needs, and a missed slot while the app was closed is skipped rather than
fired late in a burst.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules(
  id TEXT PRIMARY KEY, goal TEXT NOT NULL, cadence TEXT NOT NULL, at TEXT,
  next_run REAL NOT NULL, created_at REAL NOT NULL, last_run REAL, runs INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1, last_run_id TEXT);
"""
CADENCES = ("once", "daily", "weekdays", "weekly", "hourly")


def parse_when(cadence: str, at: str, now: datetime | None = None) -> float:
    """Next fire time as a unix timestamp. `at` is 'HH:MM' for recurring, ISO for once."""
    now = now or datetime.now()
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {', '.join(CADENCES)}")
    if cadence == "once":
        when = datetime.fromisoformat(at)
        if when <= now:
            raise ValueError("that time is already past")
        return when.timestamp()
    if cadence == "hourly":
        return (now + timedelta(hours=1)).timestamp()
    try:
        hh, mm = (int(x) for x in at.split(":"))
    except ValueError as e:
        raise ValueError("`at` must look like 08:30") from e
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    if cadence == "weekdays":
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    return nxt.timestamp()


class Scheduler:
    def __init__(self, db_path: Path, launch: Callable[[str], Awaitable[str] | str]):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.launch = launch
        self._task: asyncio.Task | None = None

    def add(self, goal: str, cadence: str = "once", at: str = "") -> dict:
        sid = uuid.uuid4().hex[:10]
        nxt = parse_when(cadence, at)
        self.db.execute("INSERT INTO schedules(id,goal,cadence,at,next_run,created_at) VALUES(?,?,?,?,?,?)",
                        (sid, goal, cadence, at, nxt, time.time()))
        self.db.commit()
        return {"id": sid, "goal": goal, "cadence": cadence, "at": at, "next_run": nxt}

    def cancel(self, schedule_id: str) -> bool:
        cur = self.db.execute("UPDATE schedules SET active=0 WHERE id=? AND active=1", (schedule_id,))
        self.db.commit()
        return cur.rowcount > 0

    def list(self, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM schedules" + ("" if include_inactive else " WHERE active=1") + " ORDER BY next_run"
        return [dict(r) for r in self.db.execute(sql)]

    def due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM schedules WHERE active=1 AND next_run<=?", (now,))]

    def _advance(self, row: dict, run_id: str) -> None:
        if row["cadence"] == "once":
            self.db.execute("UPDATE schedules SET active=0, last_run=?, runs=runs+1, last_run_id=? WHERE id=?",
                            (time.time(), run_id, row["id"]))
        else:
            self.db.execute("UPDATE schedules SET next_run=?, last_run=?, runs=runs+1, last_run_id=? WHERE id=?",
                            (parse_when(row["cadence"], row["at"]), time.time(), run_id, row["id"]))
        self.db.commit()

    async def tick(self) -> list[str]:
        started = []
        for row in self.due():
            run_id = self.launch(row["goal"])
            if asyncio.iscoroutine(run_id):
                run_id = await run_id
            self._advance(row, str(run_id))
            started.append(str(run_id))
        return started

    def start(self, interval: float = 30.0) -> None:
        async def loop():
            while True:
                with contextlib.suppress(Exception):   # a bad schedule must not kill the loop
                    await self.tick()
                await asyncio.sleep(interval)
        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.db.close()

    def as_json(self) -> str:
        return json.dumps(self.list(), default=str)
