"""Notion MCP server. Loads only when SYNAPSE_NOTION_TOKEN is set.

Create an internal integration at https://www.notion.so/my-integrations, then share the pages or
databases you want Synapse to touch with that integration (Notion returns 404 for anything unshared).
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

TOKEN = os.environ.get("SYNAPSE_NOTION_TOKEN", "")
API = "https://api.notion.com/v1"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}

mcp = FastMCP("notion", instructions="Search, read and create Notion pages and database rows.")


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    if not TOKEN:
        raise ToolError("SYNAPSE_NOTION_TOKEN is not set")
    try:
        r = httpx.request(method, f"{API}{path}", headers=HEADERS, json=payload, timeout=25)
    except httpx.HTTPError as e:
        raise ToolError(f"Notion request failed: {e}") from e
    if r.status_code == 401:
        raise ToolError("Notion rejected the token")
    if r.status_code == 404:
        raise ToolError("not found — share the page or database with your Notion integration first")
    if r.status_code >= 400:
        raise ToolError(f"Notion HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _title(obj: dict) -> str:
    props = obj.get("properties", {})
    for v in props.values():
        if v.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in v.get("title", [])) or "(untitled)"
    t = obj.get("title") or []
    return "".join(x.get("plain_text", "") for x in t) or "(untitled)"


def _blocks(markdown: str) -> list[dict]:
    """Minimal Markdown -> Notion blocks: headings, bullets, to-dos, paragraphs."""
    out = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            kind, text = "heading_3", s[4:]
        elif s.startswith("## "):
            kind, text = "heading_2", s[3:]
        elif s.startswith("# "):
            kind, text = "heading_1", s[2:]
        elif s.startswith(("- [ ] ", "- [x] ")):
            block = {"object": "block", "type": "to_do", "to_do": {
                "rich_text": [{"type": "text", "text": {"content": s[6:][:1900]}}], "checked": s[3] == "x"}}
            out.append(block)
            continue
        elif s.startswith(("- ", "* ")):
            kind, text = "bulleted_list_item", s[2:]
        else:
            kind, text = "paragraph", s
        out.append({"object": "block", "type": kind,
                    kind: {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}})
    return out[:100]


@mcp.tool(description="Search Notion pages and databases shared with the integration.")
def notion_search(query: str, limit: int = 5) -> dict:
    data = _call("POST", "/search", {"query": query, "page_size": min(max(limit, 1), 20)})
    return {"query": query, "results": [
        {"id": r["id"], "type": r["object"], "title": _title(r), "url": r.get("url", "")}
        for r in data.get("results", [])]}


@mcp.tool(description="Read a Notion page's text blocks.")
def notion_read_page(page_id: str, limit: int = 100) -> dict:
    data = _call("GET", f"/blocks/{page_id}/children?page_size={min(max(limit, 1), 100)}")
    lines = []
    for b in data.get("results", []):
        body = b.get(b.get("type"), {})
        text = "".join(t.get("plain_text", "") for t in body.get("rich_text", []))
        if text:
            prefix = {"to_do": "- [x] " if body.get("checked") else "- [ ] ",
                      "bulleted_list_item": "- ", "heading_1": "# ", "heading_2": "## ", "heading_3": "### "}
            lines.append(prefix.get(b["type"], "") + text)
    return {"page_id": page_id, "content": "\n".join(lines)}


@mcp.tool(description="Create a Notion page under a parent page, from Markdown (headings, bullets, - [ ] to-dos).")
def notion_create_page(parent_page_id: str, title: str, markdown: str = "") -> dict:
    data = _call("POST", "/pages", {
        "parent": {"page_id": parent_page_id},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
        "children": _blocks(markdown)})
    return {"id": data["id"], "url": data.get("url", ""), "title": title,
            "verify": {"tool": "notion_read_page", "args": {"page_id": data["id"]}}}


@mcp.tool(description="Append Markdown blocks to an existing Notion page.")
def notion_append(page_id: str, markdown: str) -> dict:
    blocks = _blocks(markdown)
    if not blocks:
        raise ToolError("nothing to append")
    _call("PATCH", f"/blocks/{page_id}/children", {"children": blocks})
    return {"page_id": page_id, "blocks_added": len(blocks)}


@mcp.tool(description="Add a row to a Notion database. Properties are plain values: text, numbers, dates, checkboxes.")
def notion_add_row(database_id: str, properties: dict) -> dict:
    schema = _call("GET", f"/databases/{database_id}").get("properties", {})
    built: dict = {}
    for key, value in properties.items():
        spec = schema.get(key)
        if not spec:
            raise ToolError(f"unknown property '{key}'. Available: {', '.join(schema)}")
        kind = spec["type"]
        if kind == "title":
            built[key] = {"title": [{"type": "text", "text": {"content": str(value)[:200]}}]}
        elif kind == "rich_text":
            built[key] = {"rich_text": [{"type": "text", "text": {"content": str(value)[:1900]}}]}
        elif kind == "checkbox":
            built[key] = {"checkbox": bool(value)}
        elif kind == "number":
            built[key] = {"number": float(value)}
        elif kind == "date":
            built[key] = {"date": {"start": str(value)}}
        elif kind in ("select", "status"):
            built[key] = {kind: {"name": str(value)}}
        elif kind == "multi_select":
            vals = value if isinstance(value, list) else [value]
            built[key] = {"multi_select": [{"name": str(v)} for v in vals]}
        else:
            raise ToolError(f"property type '{kind}' is not supported yet")
    data = _call("POST", "/pages", {"parent": {"database_id": database_id}, "properties": built})
    return {"id": data["id"], "url": data.get("url", "")}


if __name__ == "__main__":
    mcp.run()
