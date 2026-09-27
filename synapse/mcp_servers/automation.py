"""Automation MCP server: fire n8n webhooks and raise local notifications.

n8n is the escape hatch for everything Synapse has no first-party tool for — WhatsApp, Slack, Sheets,
SMS. You build the workflow in n8n behind a Webhook trigger; Synapse posts JSON to it.

Only webhooks listed in SYNAPSE_N8N_WEBHOOKS (name=url pairs, comma separated) can be called, so a
compromised plan cannot post your data to an arbitrary URL.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError


def _DB():
    from pathlib import Path
    return Path(os.environ.get("SYNAPSE_DATA_DIR", "./data")) / "schedules.db"


mcp = FastMCP("automation", instructions="Trigger pre-registered n8n workflows and send local notifications.")


def _hooks() -> dict[str, str]:
    out = {}
    for pair in os.environ.get("SYNAPSE_N8N_WEBHOOKS", "").split(","):
        if "=" in pair:
            name, url = pair.split("=", 1)
            if url.strip().startswith(("http://", "https://")):
                out[name.strip()] = url.strip()
    return out


@mcp.tool(description="List the n8n workflows Synapse is allowed to trigger, by name.")
def list_workflows() -> dict:
    hooks = _hooks()
    return {"workflows": sorted(hooks), "count": len(hooks),
            "hint": "" if hooks else "none registered; set SYNAPSE_N8N_WEBHOOKS=name=https://...,other=https://..."}


@mcp.tool(description="Trigger a registered n8n workflow by name with a JSON payload. Use for WhatsApp, Slack, SMS and other automations.")
def run_workflow(name: str, payload: dict | None = None) -> dict:
    hooks = _hooks()
    url = hooks.get(name)
    if not url:
        raise ToolError(f"workflow '{name}' is not registered. Available: {', '.join(sorted(hooks)) or '(none)'}")
    body = payload or {}
    secret = os.environ.get("SYNAPSE_N8N_SECRET", "")
    headers = {"X-Synapse-Secret": secret} if secret else {}   # n8n Webhook node -> Authentication: Header Auth
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise ToolError(f"workflow call failed: {e}") from e
    if r.status_code >= 400:
        raise ToolError(f"workflow returned HTTP {r.status_code}: {r.text[:200]}")
    try:
        result = r.json()
    except json.JSONDecodeError:
        result = {"text": r.text[:1000]}
    return {"workflow": name, "status": r.status_code, "response": result}


@mcp.tool(description="Schedule a goal to run later or on a cadence: once (ISO datetime), daily/weekdays/weekly (HH:MM), hourly.")
def schedule_task(goal: str, cadence: str = "once", at: str = "") -> dict:
    from ..scheduler import Scheduler, parse_when
    if not goal.strip():
        raise ToolError("goal is required")
    try:
        parse_when(cadence, at)
    except ValueError as e:
        raise ToolError(str(e)) from e
    sched = Scheduler(_DB(), lambda g: "")
    row = sched.add(goal.strip(), cadence, at)
    sched.db.close()
    return row | {"note": "the assistant will run this goal itself at that time"}


@mcp.tool(description="List scheduled goals with their next run time.")
def list_schedules() -> dict:
    from ..scheduler import Scheduler
    sched = Scheduler(_DB(), lambda g: "")
    rows = sched.list()
    sched.db.close()
    return {"count": len(rows), "schedules": rows}


@mcp.tool(description="Cancel a scheduled goal by id.")
def cancel_schedule(schedule_id: str) -> dict:
    from ..scheduler import Scheduler
    sched = Scheduler(_DB(), lambda g: "")
    ok = sched.cancel(schedule_id)
    sched.db.close()
    if not ok:
        raise ToolError(f"no active schedule with id {schedule_id}")
    return {"cancelled": schedule_id}


@mcp.tool(description="Show a desktop notification (macOS and Linux). Use for reminders and finished long tasks.")
def notify(title: str, message: str, subtitle: str = "") -> dict:
    title, message = title[:120], message[:400]
    system = platform.system()
    try:
        if system == "Darwin":
            script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
            if subtitle:
                script += f' subtitle {json.dumps(subtitle)}'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=True, capture_output=True, timeout=10)
        else:
            raise ToolError(f"no notification backend on {system}")
    except subprocess.CalledProcessError as e:
        raise ToolError(f"notification failed: {e.stderr[:200].decode(errors='ignore')}") from e
    return {"delivered": True, "title": title, "platform": system}


if __name__ == "__main__":
    mcp.run()
