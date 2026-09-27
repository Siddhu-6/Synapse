"""Task manager MCP server backed by SQLite."""
from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

DB = Path(os.environ.get("SYNAPSE_DATA_DIR", "./data")).expanduser().resolve() / "tasks.db"
DB.parent.mkdir(parents=True, exist_ok=True)
mcp = FastMCP("tasks", instructions="Create, list and complete the user's tasks.")


def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL, project TEXT,
        priority TEXT DEFAULT 'medium', due TEXT, notes TEXT, status TEXT DEFAULT 'open',
        created_at TEXT, completed_at TEXT, UNIQUE(title, project, status))""")
    return c


@mcp.tool(description="Add a task. priority: low|medium|high. due: ISO date (YYYY-MM-DD) or empty. Idempotent for identical open tasks.")
def add_task(title: str, project: str = "", priority: str = "medium", due: str = "", notes: str = "") -> dict:
    if priority not in ("low", "medium", "high"):
        raise ToolError("priority must be low|medium|high")
    if due:
        try:
            datetime.fromisoformat(due)
        except ValueError:
            raise ToolError("due must be an ISO date like 2026-10-01") from None
    with _db() as c:
        row = c.execute("SELECT id FROM tasks WHERE title=? AND project=? AND status='open'", (title.strip(), project)).fetchone()
        if row:
            return {"id": row["id"], "created": False, "note": "identical open task already exists"}
        cur = c.execute("INSERT INTO tasks(title,project,priority,due,notes,created_at) VALUES(?,?,?,?,?,?)",
                        (title.strip(), project, priority, due or None, notes, datetime.now(UTC).isoformat()))
        return {"id": cur.lastrowid, "created": True}


@mcp.tool(description="List tasks. status: open|done|all.")
def list_tasks(status: str = "open", project: str = "") -> dict:
    q, a = "SELECT * FROM tasks WHERE 1=1", []
    if status != "all":
        q += " AND status=?"
        a.append(status)
    if project:
        q += " AND project=?"
        a.append(project)
    q += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, due IS NULL, due"
    with _db() as c:
        rows = [dict(r) for r in c.execute(q, a).fetchall()]
    return {"count": len(rows), "tasks": rows}


@mcp.tool(description="Mark a task done by id.")
def complete_task(task_id: int) -> dict:
    with _db() as c:
        cur = c.execute("UPDATE tasks SET status='done', completed_at=? WHERE id=? AND status='open'",
                        (datetime.now(UTC).isoformat(), task_id))
        if not cur.rowcount:
            raise ToolError(f"no open task with id {task_id}")
    return {"id": task_id, "status": "done"}


if __name__ == "__main__":
    mcp.run("stdio")
