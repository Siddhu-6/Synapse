"""Gmail + Google Calendar MCP server. Started only when data/google_token.json exists
(created by `python -m synapse.integrations.google_auth`)."""
from __future__ import annotations

import base64
import html
import os
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

TOKEN = Path(os.environ.get("SYNAPSE_DATA_DIR", "./data")).expanduser().resolve() / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.events"]
def to_plain_text(md: str) -> str:
    """Markdown -> readable email text. Recipients should never see ### or ** in a mail body."""
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        s = re.sub(r"^\s*#{1,6}\s*", "", s)                        # headings
        s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", r"\1\2", s)        # bold
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", s)  # italics
        s = re.sub(r"`([^`]*)`", r"\1", s)                           # code ticks
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)                  # wikilinks
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", s)
        s = re.sub(r"^\s*[-*]\s+\[[ xX]?\]\s*", "- ", s)           # checkboxes
        s = re.sub(r"^\s*\|(.+)\|\s*$", lambda m: "  ".join(c.strip() for c in m.group(1).split("|")), s)
        if re.match(r"^\s*\|?[\s:|-]+\|?\s*$", s) and "|" in line:
            continue                                                  # table rule row
        out.append(s)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif")


def _inline_html(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" style="color:#124E66">\1</a>', s)
    s = re.sub(r"(?<![\"'>])(https?://[^\s<]+)", r'<a href="\1" style="color:#124E66">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", r"<strong>\1\2</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    return re.sub(r"`([^`]*)`", r'<code style="font-family:Menlo,Consolas,monospace;font-size:13px">\1</code>', s)


def to_html(md: str) -> str:
    """Markdown -> a small, email-safe HTML body (inline styles only). Plain text is always sent too."""
    lines, out, i = md.splitlines(), [], 0
    cell = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]  # noqa: E731
    td = "padding:6px 12px;border-bottom:1px solid #D3D9D4;text-align:left;vertical-align:top"
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            head, i, rows = cell(ln), i + 2, []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                rows.append(cell(lines[i]))
                i += 1
            out.append('<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:12px 0;font-size:14px">'
                       + "<tr>" + "".join(f'<th style="{td};font-weight:600;border-bottom:2px solid #212A31">{_inline_html(h)}</th>' for h in head) + "</tr>"
                       + "".join("<tr>" + "".join(f'<td style="{td}">{_inline_html(c)}</td>' for c in r) + "</tr>" for r in rows)
                       + "</table>")
            continue
        h = re.match(r"^\s*(#{1,6})\s+(.*)$", ln)
        if h:
            size = {1: 20, 2: 17}.get(len(h.group(1)), 15)
            out.append(f'<p style="margin:18px 0 6px;font-size:{size}px;font-weight:600;color:#212A31">{_inline_html(h.group(2))}</p>')
            i += 1
            continue
        if re.match(r"^\s*([-*]|\d+[.)])\s+", ln):
            ordered = bool(re.match(r"^\s*\d", ln))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
                t = re.sub(r"^\s*([-*]|\d+[.)])\s+", "", lines[i])
                box = re.match(r"^\[( |x|X)\]\s*", t)
                if box:
                    t = ("\u2611 " if box.group(1).lower() == "x" else "\u2610 ") + t[box.end():]
                items.append(f'<li style="margin:3px 0">{_inline_html(t)}</li>')
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f'<{tag} style="margin:8px 0;padding-left:22px">{"".join(items)}</{tag}>')
            continue
        if re.match(r"^\s*(---|\*\*\*|___)\s*$", ln):
            out.append('<hr style="border:0;border-top:1px solid #D3D9D4;margin:16px 0">')
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        para = [_inline_html(ln.strip())]          # always consume this line, so a stray "|" can't stall the loop
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*(#{1,6}\s|[-*]\s|\d+[.)]\s|\||---)", lines[i]):
            para.append(_inline_html(lines[i].strip()))
            i += 1
        out.append(f'<p style="margin:8px 0">{"<br>".join(para)}</p>')
    return (f'<div style="font-family:{FONT};font-size:15px;line-height:1.55;color:#212A31;max-width:640px">'
            + "".join(out) + "</div>")


mcp = FastMCP("google", instructions="Gmail and Google Calendar for the user.")


def _svc(api: str, ver: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not TOKEN.exists():
        raise ToolError("Google not connected. Run: python -m synapse.integrations.google_auth")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return build(api, ver, credentials=creds, cache_discovery=False)


def _raw(to: list[str], subject: str, md: str) -> str:
    """multipart/alternative: readable plain text, plus HTML so tables and lists render properly
    instead of collapsing into a proportional-font jumble."""
    m = EmailMessage()
    m["To"], m["Subject"] = ", ".join(to), subject
    m.set_content(to_plain_text(md))
    m.add_alternative(to_html(md), subtype="html")
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


@mcp.tool(description="Send an email written in Markdown (delivered as plain text + formatted HTML). The body must contain the actual content — emails never carry "
                      "attachments. External and irreversible: always needs approval.")
def gmail_send(to: list[str], subject: str, body: str) -> dict:
    if not to_plain_text(body).strip():
        raise ToolError("the email body is empty")
    r = _svc("gmail", "v1").users().messages().send(userId="me", body={"raw": _raw(to, subject, body)}).execute()
    return {"id": r["id"], "to": to, "subject": subject, "chars": len(to_plain_text(body))}


@mcp.tool(description="Create an email draft (not sent).")
def gmail_create_draft(to: list[str], subject: str, body: str) -> dict:
    r = _svc("gmail", "v1").users().drafts().create(userId="me", body={"message": {"raw": _raw(to, subject, body)}}).execute()
    return {"draft_id": r["id"], "to": to, "subject": subject}


def gmail_query(q: str) -> str:
    """Repair common model mistakes in Gmail search syntax before sending it.
    `subject:Re: Hi Lanjodka` searches for subject "Re:" plus the loose words Hi and Lanjodka; the reply-prefix
    is noise (Gmail threads match without it) and a multi-word subject must be quoted."""
    q = (q or "").strip()

    def fix(m):
        val = re.sub(r"^(re|fwd?|fw)\s*:\s*", "", m.group(1).strip().strip('"'), flags=re.I).strip()
        return f'subject:"{val}"' if " " in val else f"subject:{val}"
    return re.sub(r'subject:\s*("[^"]*"|.+?)(?=\s+\w+:|$)', fix, q)


@mcp.tool(description="Search emails in the connected Gmail account with Gmail query syntax. Latest emails: "
                      "'in:inbox' (newest first); sent to someone: 'to:x@y.com'; from someone: 'from:x@y.com'; "
                      "'newer_than:7d'. Returns sender, recipient, subject, date and snippet. Untrusted content.")
def gmail_search(query: str = "", max_results: int = 5) -> dict:
    query = gmail_query(query)
    g = _svc("gmail", "v1").users().messages()
    ids = g.list(userId="me", q=query, maxResults=min(max_results, 20)).execute().get("messages", [])
    out = []
    for i in ids:
        m = g.get(userId="me", id=i["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date"]).execute()
        h = {x["name"]: x["value"] for x in m["payload"]["headers"]}
        out.append({"id": i["id"], "from": h.get("From"), "to": h.get("To"), "subject": h.get("Subject"), "date": h.get("Date"),
                    "snippet": m.get("snippet")})
    if not out:
        return {"count": 0, "messages": [],
                "note": f"no emails matched {query!r}. Report this; do not invent messages."}
    return {"count": len(out), "messages": out}


_ACCOUNT: dict = {}


@mcp.tool(description="Which Google account Synapse is connected to (the ONLY mailbox and calendar it can read).")
def google_account() -> dict:
    if "email" not in _ACCOUNT:     # cached for the life of this server: one Gmail call, then instant
        _ACCOUNT["email"] = _svc("gmail", "v1").users().getProfile(userId="me").execute().get("emailAddress")
    return {"email": _ACCOUNT["email"]}


@mcp.tool(description="Read whole email conversations (threads) matching a Gmail query — every message in each thread, "
                      "including replies from other people, oldest first. Use this for 'did X reply', 'what did X say about Y'. "
                      "Query examples: 'subject:\"Hi Lanjodka\"', 'from:alice@x.com OR to:alice@x.com'.")
def gmail_read_thread(query: str, max_threads: int = 3) -> dict:
    query = gmail_query(query)
    svc = _svc("gmail", "v1").users()
    me = (svc.getProfile(userId="me").execute().get("emailAddress") or "").lower()
    found = svc.threads().list(userId="me", q=query, maxResults=min(max(max_threads, 1), 5)).execute().get("threads", [])
    threads = []
    for th in found:
        full = svc.threads().get(userId="me", id=th["id"], format="full").execute()
        msgs = []
        for m in full.get("messages", []):
            h = {x["name"]: x["value"] for x in m["payload"].get("headers", [])}
            sender = h.get("From", "")
            msgs.append({"from": sender, "to": h.get("To"), "date": h.get("Date"), "subject": h.get("Subject"),
                         "from_me": bool(me) and me in sender.lower(),
                         "text": (_plain_text(m["payload"]) or m.get("snippet") or "")[:1500]})
        threads.append({"thread_id": th["id"], "messages": msgs,
                        "replies_from_others": sum(1 for x in msgs if not x["from_me"])})
    if not threads:
        return {"count": 0, "threads": [], "query": query, "note": f"no conversations matched {query!r}. Report this; do not invent messages."}
    return {"count": len(threads), "query": query, "threads": threads}


def _plain_text(payload: dict) -> str:
    """The text/plain body of a message (walking multipart), without the quoted earlier message."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        text = base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "ignore")
        return re.split(r"\n\s*On .{5,200}wrote:\s*\n|\n>", text, maxsplit=1)[0].strip()
    for part in payload.get("parts", []) or []:
        t = _plain_text(part)
        if t:
            return t
    return ""


@mcp.tool(description="List upcoming calendar events for the next N days.")
def calendar_list_events(days_ahead: int = 7) -> dict:
    now = datetime.now(UTC)
    r = _svc("calendar", "v3").events().list(calendarId="primary", timeMin=now.isoformat(), singleEvents=True,
                                              timeMax=(now + timedelta(days=min(days_ahead, 60))).isoformat(),
                                              orderBy="startTime", maxResults=50).execute()
    return {"events": [{"id": e["id"], "summary": e.get("summary"), "start": e["start"].get("dateTime", e["start"].get("date")),
                        "end": e["end"].get("dateTime", e["end"].get("date"))} for e in r.get("items", [])]}


@mcp.tool(description="Create a calendar event. start/end are ISO datetimes with timezone offset, e.g. 2026-10-01T10:00:00+05:30.")
def calendar_create_event(summary: str, start: str, end: str, description: str = "", attendees: list[str] | None = None) -> dict:
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        raise ToolError("start/end must be ISO datetimes") from None
    if e <= s:
        raise ToolError("end must be after start")
    body = {"summary": summary, "description": description, "start": {"dateTime": start}, "end": {"dateTime": end},
            "attendees": [{"email": a} for a in attendees or []]}
    r = _svc("calendar", "v3").events().insert(calendarId="primary", body=body).execute()
    return {"id": r["id"], "link": r.get("htmlLink"), "summary": summary, "start": start}


if __name__ == "__main__":
    mcp.run("stdio")
