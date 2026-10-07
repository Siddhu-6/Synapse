"""Core runtime graph.

recall -> plan -> step (loop, one node per plan step, HIL interrupt before risky calls)
       -> verify -> [replan -> plan] (bounded) -> respond -> memorize
"""
from __future__ import annotations

import asyncio
import json
import operator
import re
import time
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator

from . import guardrails as G
from . import identity as ID
from .config import Settings
from .crew import CREWS, crew_catalog, run_crew
from .llm import LLM, LLMError, LLMResponse, structured
from .memory import Memory
from .tools import Toolbox


# ---------------- contracts ----------------
class Step(BaseModel):
    id: str = Field(description="s1, s2, ... in order")
    kind: Literal["tool", "generate", "crew"]
    description: str
    tool: str | None = Field(default=None, description="kind=tool: tool name")
    args: dict[str, Any] = Field(default_factory=dict, description="kind=tool: arguments; may contain {{sN}}")
    prompt: str | None = Field(default=None, description="kind=generate|crew: instruction; may contain {{sN}}")
    crew: str | None = Field(default=None, description="kind=crew: research | study | review | vault | decide")


class Triage(BaseModel):
    mode: Literal["chat", "task"] = Field(description="chat = answer from your own knowledge; task = needs tools or steps")
    answer: str | None = Field(default=None, description="For chat only: the complete answer")


class Plan(BaseModel):
    intent: str = Field(description="What the user wants, one sentence")
    deliverable: str = Field(description="Concrete expected output")
    reasoning: str = Field(description="Brief rationale")
    steps: list[Step] = Field(default_factory=list)
    direct_answer: str | None = Field(default=None, description="Only when no steps are needed: the full answer")

    @model_validator(mode="after")
    def _coerce(self):
        for st in self.steps:          # models often write {s1.body}; accept it as {{s1.body}}
            if st.args:
                st.args = json.loads(SINGLE_REF.sub(r"{{\1}}", json.dumps(st.args)))
            if st.prompt:
                st.prompt = SINGLE_REF.sub(r"{{\1}}", st.prompt)
        """Small models often put the instruction in `description` and omit `prompt`. That is
        recoverable, so repair it instead of burning a retry on the model."""
        for st in self.steps:
            if st.kind in ("generate", "crew") and not (st.prompt or "").strip():
                st.prompt = st.description
            if st.kind == "tool" and st.args is None:
                st.args = {}
        return self


class ArgFix(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict, description="Corrected arguments for the tool")


class Facts(BaseModel):
    facts: list[dict[str, str]] = Field(default_factory=list, description='[{"text": ..., "kind": "preference|profile|project"}]')


class AgentState(TypedDict, total=False):
    capabilities: dict
    note_titles: list[str]
    note_paths: list[str]
    history: list[dict]
    conversation_id: str
    run_id: str
    goal: str
    memory: dict
    user: dict                      # {name, email, facts}: who drafts are written for
    plan: dict
    cursor: int
    results: dict[str, dict]
    tainted: list[str]
    verification: dict
    replans: int
    llm_calls: int
    failure_context: str
    status: Literal["success", "failed"]
    answer: str
    error: str
    events: Annotated[list[dict], operator.add]
    approvals: Annotated[list[dict], operator.add]


REF = re.compile(r"\{\{\s*(s\d+)(?:\.([a-zA-Z_][\w]*))?\s*\}\}")
SINGLE_REF = re.compile(r"(?<!\{)\{(s\d+(?:\.[a-zA-Z_]\w*)?)\}(?!\})")
WRITE_TOOLS = {"create_note", "append_to_note", "update_note"}


ATTACH_PHRASE = re.compile(r"\b(attach(ed|ment|ments|ing)?|enclosed|see (the )?(file|note) below)\b", re.I)
ATTACH_SENTENCE = re.compile(
    r"[^.!?\n]*\b(attach(ed|ment|ments|ing)?|enclosed)\b[^.!?\n]*[.!?]?[ \t]*", re.I)
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n+", re.S)


def inline_email_body(body: str, content: str | None) -> str:
    """Emails carry text, never attachments.

    Models write "Please find attached the roadmap" instead of referencing the step that produced it,
    and the recipient gets a one-line email with nothing in it. So the attachment sentence is dropped
    and the real content is put in the body — before the approval prompt, so what the user approves
    is the email that will actually be sent."""
    if not ATTACH_PHRASE.search(body or ""):
        return body
    kept = ATTACH_SENTENCE.sub("", body).strip()
    if not content:
        return kept or body
    content = FRONTMATTER.sub("", content).strip()
    if content[:160] in body:                       # already inlined via {{sN}}; just drop the phrase
        return kept or body
    return f"{kept}\n\n{content}".strip() if kept else content


STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "your", "list", "note", "notes", "about"}


def relevant_titles(titles: list[str], context: str, limit: int = 8) -> list[str]:
    """Vault titles worth offering the writer as [[links]].

    Handing over every title made the model sprinkle unrelated links (a roadmap linking to Messi and
    NASA notes), so a title has to share a real word with the task to be offered at all."""
    words = {w for w in re.findall(r"[a-z]{4,}", context.lower()) if w not in STOPWORDS}
    scored = []
    for title in titles:
        tw = {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in STOPWORDS}
        overlap = len(words & tw)
        if overlap:
            scored.append((overlap, title))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [tt for _, tt in scored[:limit]]


def known_addresses(state: dict) -> set[str]:
    """Addresses the user has already used in this chat or that memory holds — not from web content."""
    user = state.get("user") or {}
    text = " ".join([state.get("goal", ""), user.get("email") or ""]
                    + [h.get("goal", "") for h in state.get("history") or []]
                    + [f.get("text", "") for f in (state.get("memory") or {}).get("facts", [])]
                    + [f.get("text", "") for f in user.get("facts") or []])
    raw = {e.lower() for e in G.EMAIL.findall(text)}
    return raw | {G.clean_address(e, raw) for e in raw}


# Template filler a model writes when it had no data: "[Insert Temperature Here]", "[Your Name]", "TBD".
PLACEHOLDER = re.compile(r"\[(insert|your|add|enter|fill|replace|placeholder)\b[^\]\n]{0,60}\]|\{\{\s*\w+\s*\}\}"
                         r"|\b(are|is) placeholders?\b|\bplaceholder (text|data|values?)\b", re.I)
LINK_ONLY_LINE = re.compile(r"^\s*(related( notes)?:?\s*)?(\[\[[^\]]+\]\][\s,·|-]*)+$", re.I | re.M)


def email_ready(body: str) -> tuple[str, str | None]:
    """Clean an email body and say why it must not be sent. Vault [[links]] mean nothing in an inbox,
    so link-only lines go; placeholders mean the data was never fetched, so the step fails and replans."""
    body = LINK_ONLY_LINE.sub("", body or "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    m = PLACEHOLDER.search(body)
    if m:
        return body, (f"the message contains placeholder text ({m.group(0)!r}) instead of real data. Fetch the data first "
                      "with a tool step (weather(city), list_tasks, calendar_list_events, gmail_search, read_note) and "
                      "pass it with {{sN}}, or leave that part out. Never send template text.")
    return body, None
def user_context(state: dict, answer_chars: int = 1200) -> str:
    """What the writer needs to draft IN the user's voice: who they are and what was just said.
    Without it the writer signed messages "[Your Name]" and could not resolve "send it to him"."""
    user = state.get("user") or {}
    lines = []
    if user.get("name"):
        lines.append(f"- Name: {user['name']}")
    if user.get("email"):
        lines.append(f"- Own email: {user['email']}")
    for f in user.get("facts") or []:
        if f.get("text") and (not user.get("name") or user["name"].lower() not in f["text"].lower()):
            lines.append(f"- {f['text']}")
    out = ""
    if lines:
        out += "The user you are writing for (anything you draft is sent in their name):\n" + "\n".join(lines[:10]) + "\n\n"
    if state.get("history"):
        out += "Earlier in this chat (most recent last):\n" + "\n".join(
            f"USER: {h['goal']}\nSYNAPSE: {(h.get('answer') or '')[:answer_chars]}" for h in state["history"][-2:]) + "\n\n"
    return out


def personalise(v: Any, user: dict) -> Any:
    """Put the user's name/email into template slots of outgoing text (deterministic, no LLM)."""
    if isinstance(v, str):
        return ID.fill(v, user.get("name"), user.get("email"))
    if isinstance(v, dict):
        return {k: personalise(x, user) for k, x in v.items()}
    if isinstance(v, list):
        return [personalise(x, user) for x in v]
    return v


OUTGOING_TEXT = {"gmail_send": ("subject", "body"), "gmail_create_draft": ("subject", "body"),
                 "run_workflow": ("payload",), "notify": ("title", "message", "subtitle")}


# Goals that promise a lasting artefact. A plan with no write tool cannot satisfy them.
# (Not the bare words "vault"/"obsidian": "delete the vault note about X" is not a request to save.)
SAVE_INTENT = re.compile(r"\b(save|saving|store (it|this|them)|"
                         r"(write|add|put) (it|this|them|the \w+) (to|in|into)|"
                         r"create (a|the) note|make (a|the) note|note it down)\b", re.I)
DELETE_INTENT = re.compile(r"\b(delete|remove|trash|get rid of|erase)\b", re.I)


def wants_save(goal: str) -> bool:
    return bool(SAVE_INTENT.search(goal or "")) and not DELETE_INTENT.search(goal or "")


ACTION_WORDS = re.compile(r"^\s*(create|save|write|update|append|store|add|delete|remove|send|schedule)\b", re.I)
FIRST_PERSON = re.compile(r"\b(i|i'm|im|my|me|mine|we|our)\b", re.I)


# ---------------- helpers ----------------
SLASH = re.compile(r"^\s*/(research|study|learn|review|edit|vault|notes|decide)\b\s*", re.I)
SLASH_CREW = {"research": "research", "study": "study", "learn": "study", "review": "review", "edit": "review",
              "vault": "vault", "notes": "vault", "decide": "decide"}


def slash_crew(goal: str) -> str | None:
    """'/study LangGraph' -> 'study'. A slash command is an explicit request for that crew."""
    m = SLASH.match(goal or "")
    return SLASH_CREW[m.group(1).lower()] if m else None


def now_line() -> str:
    """The real date and time. Models only know their training year (they wrote "due 2024-03-01" in 2026),
    so every prompt that may mention a date gets this line."""
    n = datetime.now().astimezone()
    return (f"Current date and time: {n:%A, %d %B %Y, %H:%M} ({n.tzname()}, UTC{n:%z}). "
            f"The current year is {n.year}. Any date you write must be computed from this, never from your training data.")


URL = re.compile(r"https?://[^\s)\]>\"'`]+")
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
TRACKER_LINE = re.compile(r"^\s*[-*]?\s*\**\s*(due( date)?|deadline|priority|status|estimated time|time estimate)\s*\**\s*:.*$",
                          re.I | re.M)
TRACKER_ASK = re.compile(r"\b(to-?do|task|deadline|due|priorit|schedule|timeline|tracker|dates?)\b", re.I)


def _norm(u: str) -> str:
    return u.rstrip(".,;:!?").split("#")[0].rstrip("/").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")


def tidy_answer(text: str, goal: str, allowed_urls: set[str]) -> str:
    """Deterministic clean-up of the final answer, because prompts alone did not stop it:
    - links: keep only URLs that came out of a tool this run (pages actually fetched); models invent URLs
      that 404. An invented markdown link keeps its text, a bare invented URL is dropped.
    - tracker noise: "Due Date / Priority / Status" lines and "- [ ]" boxes on answers that never asked
      for a to-do list or schedule."""
    ok = {_norm(u) for u in allowed_urls}
    text = MD_LINK.sub(lambda m: m.group(0) if _norm(m.group(2)) in ok else m.group(1), text or "")
    text = URL.sub(lambda m: m.group(0) if _norm(m.group(0)) in ok else "", text)
    text = re.sub(r"\((?:source|see|via)?:?\s*\)", "", text, flags=re.I)
    if not TRACKER_ASK.search(goal or ""):
        text = TRACKER_LINE.sub("", text)
        text = re.sub(r"^(\s*)[-*] \[[ xX]\] ", r"\1- ", text, flags=re.M)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def note_key(path_or_title: str) -> str:
    """"Sid's Personality.md", "People/sids personality (Synapse).md", "Sid's personality 2" -> "sid s personality"."""
    stem = path_or_title.rsplit("/", 1)[-1].removesuffix(".md").lower()
    stem = re.sub(r"\((synapse|copy|\d+)\)|\b(copy|new|v?\d+)$", "", stem.strip())
    return " ".join(re.findall(r"[a-z0-9]+", stem.replace("'s", "s")))


def same_subject(path: str, existing: list[str]) -> str | None:
    """An existing note about the same subject as `path` (same title, any folder, any "(Synapse)"/"2" suffix)."""
    k = note_key(path)
    if not k:
        return None
    return next((e for e in existing if note_key(e) == k and e != path), None)


def related_notes(existing: list[str], text: str, limit: int = 8) -> list[str]:
    """Vault paths whose title shares real words with the request — shown to the planner so it edits
    the note that is already there instead of creating a second one."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", text.lower().replace("'s", "s")) if w not in STOPWORDS}
    scored = []
    for p in existing:
        overlap = len(words & set(note_key(p).split()))
        if overlap:
            scored.append((-overlap, p))
    return [p for _, p in sorted(scored)[:limit]]


def run_urls(results: dict) -> set[str]:
    """Every URL that appeared in a tool output during this run (research sources, search results, notes)."""
    found: set[str] = set()
    for r in (results or {}).values():
        if r.get("kind") == "tool" and r.get("status") == "ok":
            found |= set(URL.findall(json.dumps(r.get("output"), ensure_ascii=False, default=str)))
    return found


def streamer(live_fn, state, every: float = 0.25, **fields):
    """on_token callback that pushes the text being written to the dashboard, at most every `every` seconds."""
    last = [0.0]

    def on_token(text: str) -> None:
        now = time.monotonic()
        if now - last[0] >= every:
            last[0] = now
            live_fn(state, partial=text, **fields)
    return on_token


def llm_chat(llm, messages, max_tokens=None, on_token=None):
    """Stream when the provider supports it (real models do; test doubles may not)."""
    if on_token and getattr(llm, "streams", False):
        return llm.chat(messages, max_tokens=max_tokens, on_token=on_token)
    return llm.chat(messages, max_tokens=max_tokens)


def _ev(node: str, t0: float, calls: list[LLMResponse] = (), **extra) -> dict:
    e = {"node": node, "ts": time.time(), "latency_ms": int((time.perf_counter() - t0) * 1000), **extra}
    if calls:
        e["llm"] = [c.model_dump(exclude={"text"}) for c in calls]
    return e


def validate_plan(plan: Plan, tool_names: set[str], max_steps: int, crew_enabled: bool, goal: str = "") -> list[str]:
    errs, seen = [], []
    if not plan.steps and not plan.direct_answer:
        errs.append("plan needs steps or a direct_answer")
    if len(plan.steps) > max_steps:
        errs.append(f"{len(plan.steps)} steps; max is {max_steps}")
    for s in plan.steps:
        if s.id in seen:
            errs.append(f"duplicate step id {s.id}")
        if s.kind == "tool" and s.tool not in tool_names:
            errs.append(f"{s.id}: unknown tool '{s.tool}'. Allowed: {sorted(tool_names)}")
        if s.kind in ("generate", "crew") and not s.prompt:
            errs.append(f"{s.id}: {s.kind} step needs a prompt")
        if s.kind == "crew" and not crew_enabled:
            errs.append(f"{s.id}: crew steps are disabled; use generate")
        if s.kind == "crew" and s.crew and s.crew not in CREWS:
            errs.append(f"{s.id}: unknown crew {s.crew!r}; choose one of {sorted(CREWS)}")
        for ref, _field in REF.findall(json.dumps(s.args) + (s.prompt or "")):
            if ref not in seen:
                errs.append(f"{s.id}: references {ref} before it exists")
        if s.kind == "tool" and s.tool in ("gmail_send", "gmail_create_draft"):
            to = (s.args or {}).get("to")
            for addr in ([to] if isinstance(to, str) else to or []):
                if isinstance(addr, str) and "{{" not in addr and "@" not in addr:
                    errs.append(f"{s.id}: {s.tool} only sends email and {addr!r} is not an email address. "
                                "WhatsApp/SMS go through run_workflow with a registered workflow; if none is registered, "
                                "return no steps and say so in direct_answer.")
        if s.kind in ("generate", "crew") and ACTION_WORDS.match(s.description or ""):
            errs.append(f"{s.id}: kind={s.kind} only produces text and cannot act, but its description promises an action "
                        f"({s.description!r}). Use kind=tool with the right tool, or reword it as drafting text.")
        seen.append(s.id)
    if plan.steps and wants_save(goal) and not any(
            st.kind == "tool" and st.tool in WRITE_TOOLS for st in plan.steps):
        errs.append("the goal asks for something to be saved, so the plan MUST include a vault write tool "
                    f"({', '.join(sorted(WRITE_TOOLS & tool_names))}). kind=generate does not write files.")
    return errs


INTERNAL_KEYS = ("verify", "sha256", "expect_sha256", "bytes", "id")


def output_text(out: Any) -> str:
    """Readable text form of a step output, used for {{sN}} substitution.

    Internal bookkeeping (hashes, verify recipes, ids) is stripped: it used to leak into emails."""
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        out = {k: v for k, v in out.items() if k not in INTERNAL_KEYS}
        if "content" in out and set(out) <= {"content", "path"}:
            return out["content"]
        if "report" in out:
            return out["report"]
        if "sources" in out:
            return "\n\n".join(f"### {s['title']}\nURL: {s['url']}\n{s['text']}" for s in out["sources"])
        if "notes" in out and isinstance(out["notes"], list) and out["notes"] and isinstance(out["notes"][0], dict):
            return "\n\n".join(f"### [[{n['path'][:-3]}]]\n{n['content']}" for n in out["notes"])
        if "results" in out and isinstance(out["results"], list):
            return "\n".join(f"- {r.get('title') or r.get('path')} {r.get('url', '')} {r.get('snippet', '')}" for r in out["results"])
        if "content" in out:
            return out["content"]
        if "text" in out:
            return f"{out.get('title', '')}\n{out.get('url', '')}\n{out['text']}".strip()
    return json.dumps(out, ensure_ascii=False, default=str)


def _render(template: str, results: dict, tainted: set[str], wrap: bool) -> tuple[str, bool]:
    used_taint = False

    def sub(m):
        nonlocal used_taint
        sid, field = m.group(1), m.group(2)
        if sid not in results or results[sid].get("status") != "ok":
            return m.group(0)
        out = results[sid].get("output")
        if field:  # {{s1.sha256}} pulls one field out of a tool result instead of the whole output
            if not isinstance(out, dict) or field not in out:
                return m.group(0)
            return str(out[field])
        txt = output_text(out)
        if not txt.strip() or txt.strip() in ("{}", "[]", '{"results": []}'):
            txt = "(this tool returned NO DATA — say so; do not invent results)"
        if sid in tainted:
            used_taint = True
            if wrap:
                return G.wrap_untrusted(txt, results[sid].get("tool") or sid)
        return txt

    return REF.sub(sub, template), used_taint


def _render_args(v: Any, results: dict, tainted: set[str]) -> tuple[Any, bool]:
    if isinstance(v, str):
        return _render(v, results, tainted, wrap=False)
    if isinstance(v, dict):
        out, t = {}, False
        for k, x in v.items():
            out[k], tt = _render_args(x, results, tainted)
            t |= tt
        return out, t
    if isinstance(v, list):
        pairs = [_render_args(x, results, tainted) for x in v]
        return [p[0] for p in pairs], any(p[1] for p in pairs)
    return v, False


def _status_lines(results: dict) -> str:
    return "\n".join(f"- {sid} [{r['status']}] {r['description']}" + (f": {r['error']}" if r.get("error") else "")
                     for sid, r in results.items()) or "(nothing executed)"


WRITER_RULES = """You are Synapse's writer. Output ONLY the requested content as clean Markdown.
- Tasks, comparisons, schedules, roadmaps and anything with repeating fields go in a Markdown table.
- To-do lists use checkboxes: "- [ ] task" (one per line), with a table when items have due dates or priorities.
- Do NOT add due dates, deadlines, priorities or "Status" fields unless the user asked for them. Learning topics,
  explanations and recommendations are plain headings and bullets, not a task tracker.
- When a date is needed, work it out from the current date given to you. Never use a year from your training data.
- When recommending topics, skills or technologies, name the specific tools, frameworks, libraries and protocols for
  each one (from the sources where given), e.g. "Agent orchestration — LangGraph, CrewAI, AutoGen", not just the category.
- Use ## headings for sections, `-` for plain bullets, **bold** sparingly, and fenced code blocks for code.
- Link related vault notes with [[Note Name]] so they connect in Obsidian's graph. Only link notes that exist.
- If the task is a question, ANSWER it in the first line, then give supporting detail and the source URLs.
  Never reply with steps the user should take to find the answer themselves.
- If the sources do not contain the answer, say so plainly and state what is missing.
- NEVER write placeholders such as "[Insert Temperature Here]" or "[Your Name]". If you were not given a value,
  leave that line or section out entirely.
- A message or email you draft is sent in the user's name: sign it with their name exactly as given to you. If no
  name is given, end without a signature. Greet the recipient by name only if you were told it.
- NEVER invent tasks, meetings, emails, senders, dates, URLs, quotes or numbers. Facts about the user's data, and
  anything current (prices, versions, news, who holds a role), must come from the step outputs you were given. If a
  tool returned no data, write exactly that and stop.
- Notes about a person (the user, a friend, family) contain ONLY what the user actually said. Never add traits,
  hobbies, events or background they did not state — a short true note beats a long invented one.
- For general technical knowledge you may add well-established tools, frameworks and concepts the sources left out
  (e.g. LangChain, LangGraph, LlamaIndex, MCP) — but never invent URLs or statistics for them.
- Emails never have attachments. Never write "attached" or "please find attached" — put the content itself in the email.
- No preamble, no "here is", no closing summary. Start with the content itself.
"""

PLANNER_RULES = """Rules:
- Use ONLY the listed tools, with argument names exactly as listed. Step ids are s1, s2, ... in order.
- kind=tool calls a tool. kind=generate ONLY produces text in memory: it cannot create, save or change anything.
  Anything touching the vault, tasks, email or calendar MUST be kind=tool with a real tool name.
- kind=crew runs a CrewAI team of specialist agents (set "crew"). Teams with tools find their own sources, so a
  crew step can be the first step. Crews only read; save/send results with a following tool step.
  Crews: CREW_CATALOG.
  A crew takes 30s-2min: use one when the user wants depth (a report, a study guide, a decision, a polished draft,
  a synthesis of their notes) — not for quick questions, and never to check something already written.
- Pass an earlier output with {{sN}} inside args or prompts, e.g. create_note content "{{s2}}".
- Pass ONE field of an earlier tool result with {{sN.field}}. Never invent hashes, ids or paths:
  to change an existing note, plan read_note(path) then update_note(path, content, expected_sha256="{{s1.sha256}}").
- A question about the world ("who/what/when/which team...") is answered from sources, not delegated back to the user:
  s1 tool research(query) -> s2 generate that ANSWERS the question from {{s1}} with the source URLs. Never plan a
  checklist telling the user to go and look something up.
- Requests to suggest, recommend, list, or compare technologies, tools, frameworks, topics, or learning paths
  MUST use research: s1 tool research(query) -> s2 generate a comprehensive, practical answer from {{s1}} that names
  the specific current tools, frameworks and libraries for each topic. Make the research query ask for tools and
  frameworks, e.g. "AI engineer roadmap 2026 tools frameworks RAG agents LLMOps".
  Your own knowledge of specific tools and frameworks is incomplete — always research first.
- research(query) (search + fetch top pages) beats web_search alone: snippets are often too thin to answer from.
- Existing notes: gather_notes(query) returns full note contents to summarise/study.
- To-do lists, checklists and comparisons: generate them as a Markdown TABLE. Do NOT add a save step unless the
  user asked to save/store/note it — an unrequested save stops the run to wait for their approval.
- Notion: notion_search to find a page/database, notion_create_page for a new page, notion_add_row for a database row.
- Automation: list_workflows shows registered n8n workflows; run_workflow(name, payload) triggers one (WhatsApp,
  Slack, SMS and anything else wired in n8n). Payload convention: {"to": "<recipient>", "message": "<text>"}
  (add "subject" if relevant); put the finished text itself in "message", e.g. "{{s1}}". notify(title, message) raises a desktop notification.
- Organising: vault_stats / list_folders / related_notes to inspect, move_note to rename or file a note,
  replace_section to rewrite one "## heading" in place, add_tags for frontmatter tags.
- Note paths are vault-relative Markdown paths like "Research/LangGraph vs CrewAI.md". Link related notes with [[Note Name]].
- If the user asks to save/store anything, the plan MUST include create_note (or append_to_note/update_note).
- Before create_note, check "Notes that ALREADY EXIST". If the note is already there, NEVER create another:
  new facts -> append_to_note(path, content); rewrite/merge -> read_note(path) then update_note(path, content,
  expected_sha256="{{s1.sha256}}").
- "Combine/merge these notes into one": gather_notes(query) -> generate ONE merged note from {{s1}} (keep every fact,
  drop duplicates) -> update_note or create_note for the kept note -> delete_note for each duplicate path.
- To delete notes ("delete the note/vault note about X"): one delete_note(path) step per matching note, using the
  exact paths from "Notes that ALREADY EXIST" (or search_notes first if none are listed). No write step is needed.
  Deleted notes go to .trash and each delete needs the user's approval.
- One note per deliverable: generate the content once, then write it once. Never add a second "save" step.
- Keep step descriptions under 10 words. Do not restate the goal in every step.
- At most ONE research step. Prefer 2-3 steps total; more steps means a slower, more fragile run.
- For a question or explanation you can answer from your own knowledge with no tools or saving, return no steps and
  put the full answer in direct_answer.
- Never plan sending email, deleting or overwriting unless the user explicitly asked for it.
- Latest emails ("last 5 mails", "my inbox"): gmail_search(query="in:inbox", max_results=N) -> generate. Mail sent
  to someone: gmail_search("to:x@y.com"); from someone: gmail_search("from:x@y.com").
- If the user asks about a DIFFERENT mailbox than the connected Gmail account ("mails in other@gmail.com"), you
  cannot read it: return no steps and say in direct_answer which account is connected, and offer to search the
  connected account for mail to/from that address instead.
- Replies and conversations ("did X reply", "what did X say about Y"): gmail_read_thread(query) returns every message
  in the thread, other people's replies included. Search by subject ('subject:"Hi Lanjodka"') and/or the person
  ('from:x@y.com OR to:x@y.com'). A reply FROM someone is from:them — to:them only finds what the user sent.
- Briefings and summaries of weather, tasks, calendar or inbox: FIRST call weather(city) / list_tasks /
  calendar_list_events / gmail_search, then a generate step whose prompt uses their outputs ({{s1}}, {{s2}}).
  Never generate such content without fetching it — that produces invented or placeholder data.
- Emails are plain text with NO attachments. The body must contain the actual content: set gmail_send body to
  "{{sN}}" where sN is the step that produced it (a generate step, or read_note of the note to send).
- Keep the plan minimal. At most {max_steps} steps."""


# ---------------- graph ----------------
def build_graph(llm: LLM, tools: Toolbox, memory: Memory, s: Settings, checkpointer=None, emit=None):
    """`emit(run_id, payload)` pushes progress to subscribers DURING a node, since LangGraph only
    streams state updates once a node returns and some nodes run for minutes."""
    def live(state, **payload):
        if emit:
            emit(state.get("run_id", ""), {"type": "progress", **payload})

    tool_names = set(tools.specs)
    planner_rules = PLANNER_RULES.replace("CREW_CATALOG", crew_catalog())

    def budget_ok(state, n=1) -> bool:
        return state.get("llm_calls", 0) + n <= s.max_llm_calls

    async def recall(state: AgentState):
        t0 = time.perf_counter()
        live(state, stage="recall")
        mem = await memory.recall(state["goal"])
        profile = memory.profile()
        said = [state["goal"]] + [h.get("goal", "") for h in reversed(state.get("history") or [])] + memory.said()
        user = {**ID.resolve(s.user_name, s.user_email, profile, said), "facts": profile}
        listing = await tools.call("list_notes", {})
        titles, paths = [], []
        if listing.ok:
            for n in (listing.data or {}).get("notes", []):
                path = n.get("path", "") if isinstance(n, dict) else str(n)
                if path:
                    titles.append(path.rsplit("/", 1)[-1].removesuffix(".md"))
                    paths.append(path)
        wf = await tools.call("list_workflows", {})
        caps = {"workflows": (wf.data or {}).get("workflows", []) if wf.ok else [],
                "notion": any(n.startswith("notion_") for n in tools.specs),
                "google": any(n.startswith(("gmail_", "calendar_")) for n in tools.specs),
                "web": "web_search" in tools.specs}
        if "google_account" in tools.specs:
            acct = await tools.call("google_account", {})
            caps["google_account"] = (acct.data or {}).get("email") if acct.ok else None
        return {"memory": mem, "user": user, "note_titles": titles[:60], "note_paths": paths[:2000], "capabilities": caps, "replans": 0, "llm_calls": 0,
                "results": {}, "tainted": [],
                "events": [_ev("recall", t0, facts=len(mem["facts"]), episodes=len(mem["episodes"]),
                               user_known=bool(user.get("name")))]}

    async def triage(state: AgentState):
        """One cheap call decides chat vs task. Greetings and general questions never reach the planner,
        which is what used to make 'Hi' cost 40 seconds and a five-step plan."""
        t0 = time.perf_counter()
        live(state, stage="triage")
        if slash_crew(state["goal"]):          # explicit crew request: no classification call needed
            return {"events": [_ev("triage", t0, mode="task", crew=slash_crew(state["goal"]))]}
        msgs = [{"role": "system", "content":
                 "First understand the user's message, then decide.\n"
                 "mode=chat — answer it yourself, now, with the FULL answer in `answer`: greetings, small talk, "
                 "thanks, opinions, and any question you can answer well from your own knowledge — explanations, "
                 "concepts, how-to, advice, learning topics, comparisons of well-known tools. Most questions are chat.\n"
                 "mode=task — leave `answer` empty — ONLY when your knowledge is not enough or an action is needed:\n"
                 "- the user asks to search / look up / research / find the latest, or gives a URL\n"
                 "- facts that change over time: news, prices, current versions, releases, who holds a role now, "
                 "anything after your training data\n"
                 "- the user's own data: notes/vault, email, calendar, tasks, files\n"
                 "- actions: create, save, send, schedule, organise, remind, automate\n"
                 "Chat answers are plain Markdown (headings, bullets). No due dates, priorities or status fields "
                 "unless asked. Do not include links you are not certain exist.\n" + now_line()}]
        if state.get("history"):
            msgs.append({"role": "user", "content": "Earlier turns:\n" + "\n".join(
                f"USER: {h['goal']}\nSYNAPSE: {(h['answer'] or '')[:300]}" for h in state["history"][-3:])})
        msgs.append({"role": "user", "content": state["goal"]})
        try:
            tri, calls = await structured(llm, msgs, Triage, retries=0, max_tokens=s.gen_max_tokens)
        except LLMError as e:
            return {"events": [_ev("triage", t0, error=str(e), mode="task")]}   # fall through to planning
        if tri.mode == "chat" and (tri.answer or "").strip():
            return {"status": "success", "answer": tidy_answer(tri.answer.strip(), state["goal"], set()), "plan": None, "results": {},
                    "verification": {"passed": True, "problems": [], "checks": []},
                    "llm_calls": state.get("llm_calls", 0) + len(calls),
                    "events": [_ev("triage", t0, calls, mode="chat", answered=True)]}
        return {"llm_calls": state.get("llm_calls", 0) + len(calls),
                "events": [_ev("triage", t0, calls, mode="task")]}

    async def plan(state: AgentState):
        t0 = time.perf_counter()
        live(state, stage="plan")
        caps = state.get("capabilities") or {}
        # "Send it on WhatsApp": either a workflow for that channel is registered, or say so plainly.
        # Without this the planner pushed WhatsApp messages through gmail_send, which the guard blocked.
        channel = ID.requested_channel(state["goal"])
        hist = state.get("history") or []
        if not channel and hist and not G.EMAIL.search(state["goal"]) and hist[-1].get("status") != "success":
            channel = ID.requested_channel(hist[-1].get("goal", ""))    # "replace it with actual data" follow-up
        channel_wf = ID.workflow_for(channel, caps.get("workflows") or []) if channel else None
        if channel and not channel_wf and not state.get("failure_context"):
            answer = ID.not_connected_answer(channel, state["goal"])
            return {"plan": {"intent": f"send a {channel} message", "deliverable": "", "reasoning": "channel not configured",
                             "steps": [], "direct_answer": answer},
                    "cursor": 0, "results": {}, "tainted": [],
                    "events": [_ev("plan", t0, steps=[], intent=f"{channel} not connected", direct=True)]}
        cap_line = ""
        if caps:
            wf = caps.get("workflows") or []
            cap_line = ("Configured right now: "
                        + f"n8n workflows: {', '.join(wf) if wf else 'NONE REGISTERED'}; "
                        + f"Notion: {'yes' if caps.get('notion') else 'no'}; "
                        + (f"Gmail/Calendar: yes, connected as {caps['google_account']} — the ONLY mailbox/calendar you can read; "
                           if caps.get("google_account") else f"Gmail/Calendar: {'yes' if caps.get('google') else 'no'}; ")
                        + f"web search: {'yes' if caps.get('web') else 'no'}.\n"
                        + "If the user asks for something whose integration is not configured, do NOT plan tool steps for it: "
                        + "return no steps and explain in direct_answer what needs configuring.\n")
        system = ("You are Synapse, a personal AI assistant that plans and executes tasks. " + now_line() + "\n"
                  + cap_line
                  + f"Tools:\n{json.dumps(tools.catalog(), separators=(',', ':'))}\n" + planner_rules.replace("{max_steps}", str(s.max_steps)))
        mem = state.get("memory") or {}
        user = ""
        if state.get("history"):
            user += ("Earlier in this chat (most recent last):\n" + "\n".join(
                f"USER: {h['goal']}\nSYNAPSE: {(h['answer'] or '')[:600]}" for h in state["history"][-3:])
                + "\n\nThe new request may refer to the above.\n\n")
        user += f"Goal: {state['goal']}"
        if slash_crew(state["goal"]):
            k = slash_crew(state["goal"])
            user += (f"\n\nThe user explicitly asked for the {CREWS[k]['name']}: plan a kind=crew step with crew=\"{k}\" "
                     "(plus any save/send step the goal asks for).")
        me = state.get("user") or {}
        known = {f["text"]: f for f in [*(me.get("facts") or []), *(mem.get("facts") or [])]}
        if me.get("name") or me.get("email"):
            user += ("\n\nThe user: " + ", ".join(x for x in [me.get("name") and f"name {me['name']}",
                                                               me.get("email") and f"own email {me['email']} (use it when they say 'me')"] if x)
                     + ". Messages you plan are sent in their name; the writer signs them. Never plan steps to look this up.")
        if known:
            user += "\n\nKnown about the user (from memory):\n" + "\n".join(f"- {t}" for t in known)
        if channel_wf:
            user += (f"\n\nThe user wants a {ID.CHANNELS.get(channel, channel)} message. Send it with "
                     f"run_workflow(name=\"{channel_wf}\", payload={{\"to\": \"<number or handle from the request>\", "
                     f"\"message\": \"{{{{sN}}}}\"}}), never with gmail_send.")
        near = related_notes(state.get("note_paths") or [], state["goal"] + " " + " ".join(
            h.get("goal", "") for h in (state.get("history") or [])[-2:]))
        if near:
            user += ("\n\nNotes that ALREADY EXIST in the vault and may be what the user means (use these exact paths):\n"
                     + "\n".join(f"- {n}" for n in near))
        if mem.get("episodes"):
            user += "\n\nRelated past runs:\n" + "\n".join(f"- {e['goal']} -> {e['status']}; {e['artifacts']}" for e in mem["episodes"])
        if state.get("failure_context"):
            user += f"\n\nThe previous attempt FAILED:\n{state['failure_context']}\nMake a corrected plan that avoids these failures."
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        calls: list[LLMResponse] = []
        errs = []
        try:
            for _ in range(2):  # one repair round for semantically invalid plans
                if not budget_ok(state, len(calls) + 1):
                    raise LLMError("LLM call budget exhausted")
                p, c = await structured(llm, msgs, Plan, max_tokens=s.plan_max_tokens)
                calls += c
                errs = validate_plan(p, tool_names, s.max_steps, s.enable_crew, state["goal"])
                if not errs:
                    return {"plan": p.model_dump(), "cursor": 0, "results": {}, "tainted": [], "llm_calls": state.get("llm_calls", 0) + len(calls),
                            "events": [_ev("plan", t0, calls, steps=[st.model_dump(exclude={"args", "prompt"}) for st in p.steps],
                                           intent=p.intent, direct=not p.steps)]}
                msgs += [{"role": "assistant", "content": p.model_dump_json()},
                         {"role": "user", "content": "Plan rejected:\n" + "\n".join(errs) + "\nReturn a corrected plan."}]
            raise LLMError("planner could not produce a valid plan: " + "; ".join(errs))
        except LLMError as e:
            return {"status": "failed", "error": str(e), "llm_calls": state.get("llm_calls", 0) + len(calls),
                    "events": [_ev("plan", t0, calls, error=str(e))]}

    def _prefetch_batch(steps: list[dict], i: int) -> list[Step]:
        """Consecutive independent read-only tool steps can run at once. They need no approval and
        are idempotent, so a checkpoint replay re-reads rather than re-writes."""
        batch: list[Step] = []
        for j in range(i, min(i + max(s.parallel_steps, 1), len(steps))):
            st = Step(**steps[j])
            if st.kind != "tool" or st.tool not in G.READ_ONLY:
                break
            if any(ref in {b.id for b in batch} for ref, _f in REF.findall(json.dumps(st.args))):
                break
            batch.append(st)
        return batch if len(batch) > 1 else []

    async def latest_content(results: dict) -> str | None:
        """The newest substantive text produced so far in this run — written text, a note that was
        read, or (by reading it back) a note that was written."""
        for r in reversed(list(results.values())):
            if r.get("status") != "ok":
                continue
            out = r.get("output")
            if r.get("kind") in ("generate", "crew"):
                return output_text(out)
            if isinstance(out, dict) and isinstance(out.get("content"), str):
                return out["content"]
            if r.get("tool") in WRITE_TOOLS and isinstance(out, dict) and out.get("path"):
                back = await tools.call("read_note", {"path": out["path"]})
                if back.ok and isinstance(back.data, dict):
                    return back.data.get("content")
        return None

    async def step(state: AgentState):
        t0 = time.perf_counter()
        steps = state["plan"]["steps"]
        i = state["cursor"]

        batch = _prefetch_batch(steps, i)
        if batch:
            live(state, stage="step", step=",".join(b.id for b in batch), index=i + 1, total=len(steps),
                 kind="parallel", tool=" + ".join(b.tool for b in batch),
                 description=f"{len(batch)} reads in parallel", actor="Researcher")
            results, tainted = dict(state["results"]), set(state.get("tainted", []))
            rendered = [_render_args(b.args, results, tainted) for b in batch]
            outs = await asyncio.gather(*(tools.call(b.tool, r[0]) for b, r in zip(batch, rendered, strict=True)))
            calls_ev = []
            for b, (args, _t), tr in zip(batch, rendered, outs, strict=True):
                base = {"description": b.description, "kind": b.kind}
                results[b.id] = ({**base, "status": "ok", "tool": b.tool, "output": tr.data} if tr.ok
                                 else {**base, "status": "failed", "tool": b.tool, "error": tr.error})
                if tr.ok and b.tool in G.UNTRUSTED_TOOLS:
                    tainted.add(b.id)
                calls_ev.append({"tool": b.tool, "args": args, "ok": tr.ok, "error": tr.error,
                                 "latency_ms": tr.latency_ms})
            failed = [b.id for b in batch if results[b.id]["status"] == "failed"]
            return {"results": results, "tainted": sorted(tainted),
                    "cursor": i + len(batch),
                    "events": [_ev("step", t0, parallel=[b.id for b in batch], tool_calls=calls_ev,
                                   status="failed" if failed else "ok",
                                   description=f"{len(batch)} parallel reads",
                                   error=f"failed: {', '.join(failed)}" if failed else None)]}

        st = Step(**steps[i])
        live(state, stage="step", step=st.id, index=i + 1, total=len(steps), kind=st.kind,
             tool=st.tool, description=st.description,
             crew=st.crew or ("research" if st.kind == "crew" else None),
             actor={"tool": st.tool or "", "generate": "Writer",
                    "crew": CREWS.get(st.crew or "research", {}).get("name", "Crew")}.get(st.kind, st.kind)
             if st.kind != "tool" else st.tool)
        results, tainted = dict(state["results"]), set(state.get("tainted", []))
        base = {"description": st.description, "kind": st.kind}
        ev: dict[str, Any] = {"step": st.id, "kind": st.kind, "description": st.description}
        calls: list[LLMResponse] = []
        approvals: list[dict] = []

        if st.kind in ("generate", "crew") and not budget_ok(state):
            results[st.id] = {**base, "status": "failed", "error": "LLM call budget exhausted"}
        elif st.kind == "generate":
            prompt, t = _render(st.prompt, results, tainted, wrap=True)
            # text that becomes an email body gets no vault links: they mean nothing in an inbox
            email_bound = any(x.get("tool") in ("gmail_send", "gmail_create_draft", "run_workflow")
                              and re.search(r"\{\{" + st.id + r"(\.\w+)?\}\}", json.dumps(x.get("args") or {}))
                              for x in steps)
            titles = [] if email_bound else relevant_titles(state.get("note_titles") or [], state["goal"] + " " + (st.prompt or ""))
            if email_bound:
                prompt += ("\n\nThis text is sent as a message/email body. Write it for the recipient: no [[links]], no notes "
                           "to yourself, no repeating these instructions. Use only data given above; drop any section "
                           "you have no data for rather than writing placeholders.")
            if titles:
                prompt += ("\n\nVault notes related to THIS task — link one only where it genuinely fits: "
                           + ", ".join(titles))
            try:
                r = await llm_chat(llm, [
                    {"role": "system", "content": WRITER_RULES + now_line() + "\n" + G.UNTRUSTED_SYSTEM_NOTE},
                    {"role": "user", "content": user_context(state) + f"Overall goal: {state['goal']}\n\nTask: {prompt}"}],
                    max_tokens=s.gen_max_tokens,
                    on_token=streamer(live, state, stage="step", step=st.id, index=i + 1, total=len(steps), kind=st.kind,
                                      description=st.description, actor="Writer",
                                      final=i == len(steps) - 1))
                calls.append(r)
                results[st.id] = {**base, "status": "ok", "output": r.text}
                if t:
                    tainted.add(st.id)
            except LLMError as e:
                results[st.id] = {**base, "status": "failed", "error": str(e)}
        elif st.kind == "crew":
            brief, t = _render(REF.sub("", st.prompt).strip() or state["goal"], results, tainted, wrap=True)
            sources = "\n\n".join(_render("{{" + sid + "}}", results, tainted, wrap=True)[0] for sid, _f in REF.findall(st.prompt))
            kind = st.crew or "research"
            if not sources and kind == "vault":
                # hand the archivist the matching notes up front; searching blind it looped on the same reads
                found = []
                for path in related_notes(state.get("note_paths") or [], state["goal"] + " " + brief)[:8]:
                    r = await tools.call("read_note", {"path": path})
                    if r.ok and isinstance(r.data, dict):
                        found.append(f"### {path}\n{(r.data.get('content') or '')[:3000]}")
                sources = "\n\n".join(found)
            if not sources and kind == "review":
                sources = user_context(state, answer_chars=4000) or "(no draft given)"   # "improve the above"
            try:
                out = await run_crew(
                    s, kind, f"{brief}\n\n{now_line()}", sources,
                    on_agent=lambda e: live(state, **{**e, "stage": "crew", "step": st.id, "crew": kind}),
                    spec=llm.crew_spec() if hasattr(llm, "crew_spec") else None, toolbox=tools)
                results[st.id] = {**base, "status": "ok", "output": out}
                ev.update(crew=kind, agents=out["agents"], crew_tokens=out["tokens"], crew_tools=out["tools_used"])
                if t or any(sid in tainted for sid, _f in REF.findall(st.prompt)) or \
                        any(x in G.UNTRUSTED_TOOLS for x in out["tools_used"]):
                    tainted.add(st.id)
            except Exception as e:
                results[st.id] = {**base, "status": "failed", "error": f"crew failed: {type(e).__name__}: {e}"}
        else:
            args, t = _render_args(st.args, results, tainted)
            if st.tool in tools.specs:
                args, renamed = G.normalize_args(tools.specs[st.tool].input_schema, args)
                if renamed:
                    ev["arg_fixes"] = renamed
            not_ready = None
            for key in OUTGOING_TEXT.get(st.tool, ()):
                if key in args:
                    args[key] = personalise(args[key], state.get("user") or {})
            if st.tool == "run_workflow" and isinstance(args.get("payload"), dict):
                for key in ("message", "text", "body"):
                    if isinstance(args["payload"].get(key), str):
                        _, why = email_ready(args["payload"][key])
                        not_ready = not_ready or why
            if st.tool in ("gmail_send", "gmail_create_draft"):
                known = known_addresses(state)
                if isinstance(args.get("to"), list):
                    args["to"] = G.clean_addresses(args["to"], known)
                elif isinstance(args.get("to"), str):
                    args["to"] = G.clean_addresses(re.split(r"[,;\s]+", args["to"]), known)
                if isinstance(args.get("body"), str):
                    args["body"], not_ready = email_ready(inline_email_body(args["body"], await latest_content(results)))
            if st.tool == "create_note" and not args.get("overwrite") and isinstance(args.get("path"), str):
                twin = same_subject(args["path"], state.get("note_paths") or [])
                if twin:   # a note on this subject exists: add to it instead of creating a duplicate
                    st = st.model_copy(update={"tool": "append_to_note"})
                    args = {"path": twin, "content": args.get("content", "")}
                    ev["redirected"] = f"note already exists: appended to {twin}"
            risk = G.assess_risk(st.tool, args)
            ev.update(tool=st.tool, risk=risk, tainted_input=t)
            block = G.check_tool_call(st.tool, args, state["goal"], t, known_addresses(state))
            decision = {"approved": True, "auto": True}
            # A call that will fail validation is not worth an approval: the user would approve, then watch it fail.
            invalid = tools.validate(st.tool, args) if G.needs_approval(risk, t) else None
            if not_ready:
                results[st.id] = {**base, "status": "failed", "tool": st.tool, "error": not_ready}
            elif block:
                results[st.id] = {**base, "status": "blocked", "tool": st.tool, "error": f"guardrail: {block}"}
            elif invalid:
                results[st.id] = {**base, "status": "failed", "tool": st.tool, "error": invalid}
                ev["skipped_approval"] = "arguments invalid"
            else:
                if G.needs_approval(risk, t):
                    decision = interrupt({"type": "approval", "step": st.id, "tool": st.tool, "args": args, "risk": risk,
                                          "tainted": t, "description": st.description,
                                          "reason": "high-risk action" if risk == "high" else "uses untrusted external content"})
                    decision = decision if isinstance(decision, dict) else {"approved": bool(decision)}
                    approvals.append({"step": st.id, "tool": st.tool, "risk": risk, "approved": bool(decision.get("approved")),
                                      "comment": decision.get("comment", ""), "ts": time.time()})
                if not decision.get("approved"):
                    results[st.id] = {**base, "status": "rejected", "tool": st.tool, "error": "rejected by user: " + decision.get("comment", "")}
                else:
                    tr = await tools.call(st.tool, args)
                    ev["tool_call"] = {"tool": tr.tool, "ok": tr.ok, "latency_ms": tr.latency_ms, "attempts": tr.attempts,
                                       "error": tr.error, "args": {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}}
                    if tr.ok:
                        results[st.id] = {**base, "status": "ok", "tool": st.tool, "output": tr.data}
                        if st.tool in G.UNTRUSTED_TOOLS:
                            tainted.add(st.id)
                            flags = G.scan_injection(output_text(tr.data))
                            if flags:
                                ev["injection_flags"] = flags
                    else:
                        # Cheap local repair: fix this one call's arguments instead of replanning the whole goal.
                        # Only for auto-approved calls — repaired arguments must never inherit an old approval.
                        if st.tool == "create_note" and tr.error and "already exists" in tr.error:
                            # The note is already there: continue inside it instead of failing the run.
                            alt = await tools.call("append_to_note", {"path": args.get("path", ""),
                                                                      "content": args.get("content", "")})
                            ev["recovered"] = "appended to the existing note"
                            if alt.ok:
                                args, tr = {**args, "mode": "append"}, alt
                                ev["tool_call"] |= {"ok": True, "tool": "append_to_note", "repaired": True}
                        if (not tr.ok and decision.get("auto") and tr.error and G.ARG_ERROR.search(tr.error)
                                and budget_ok(state, len(calls) + 1)):
                            spec = tools.specs[st.tool]
                            try:
                                fix, c = await structured(llm, [
                                    {"role": "system", "content": "Fix the tool arguments. Reply with JSON only."},
                                    {"role": "user", "content":
                                        f"Goal: {state['goal']}\nStep: {st.description}\nTool: {st.tool}\n"
                                        f"Schema: {json.dumps(spec.input_schema, separators=(',', ':'))}\n"
                                        f"Arguments tried: {json.dumps(args, default=str)[:800]}\n"
                                        f"Error: {tr.error}\nReturn the corrected arguments."}],
                                    ArgFix, retries=0, max_tokens=400)
                                calls += c
                                fixed, _ = _render_args(fix.args, results, tainted)
                                ev["arg_repair"] = {"from": tr.error, "args": list(fixed)}
                                tr2 = await tools.call(st.tool, fixed)
                                if tr2.ok:
                                    args, tr = fixed, tr2
                                    ev["tool_call"] |= {"ok": True, "repaired": True, "latency_ms": tr2.latency_ms}
                            except LLMError:
                                pass
                        if tr.ok:
                            results[st.id] = {**base, "status": "ok", "tool": st.tool, "output": tr.data}
                        else:
                            results[st.id] = {**base, "status": "failed", "tool": st.tool, "error": G.explain_error(tr.error)}
        ev["status"] = results[st.id]["status"]
        if results[st.id].get("error"):
            ev["error"] = results[st.id]["error"]
        return {"results": results, "tainted": sorted(tainted), "cursor": i + 1, "approvals": approvals,
                "llm_calls": state.get("llm_calls", 0) + len(calls), "events": [_ev("step", t0, calls, **ev)]}

    async def verify(state: AgentState):
        t0 = time.perf_counter()
        live(state, stage="verify")
        problems, checks = [], []
        results = dict(state["results"])
        for st in state["plan"]["steps"]:
            if st["id"] not in results:
                results[st["id"]] = {"description": st["description"], "kind": st["kind"], "status": "skipped"}
        for sid, r in results.items():
            if r["status"] != "ok":
                if r["status"] != "skipped":
                    problems.append(f"{sid} {r['status']}: {r.get('error', '')}")
                continue
            out = r.get("output")
            if r["kind"] in ("generate", "crew") and len(output_text(out).strip()) < 20:
                problems.append(f"{sid}: generated content is empty")
            recipe = out.get("verify") if isinstance(out, dict) else None
            if recipe:  # independent read-back proves the write landed
                rb = await tools.call(recipe["tool"], recipe["args"])
                ok = rb.ok and isinstance(rb.data, dict) and rb.data.get("sha256") == recipe.get("expect_sha256")
                checks.append({"step": sid, "check": f"{recipe['tool']}({recipe['args'].get('path')}) hash match", "passed": ok})
                if not ok:
                    problems.append(f"{sid}: write verification failed ({rb.error or 'hash mismatch'})")
        empty_sources = [sid for sid, r in results.items()
                         if r["status"] == "ok" and r.get("tool") and not str(r.get("output") or "").strip("{}[]\" ")]
        if empty_sources:
            checks.append({"step": ",".join(empty_sources), "check": "tool returned data", "passed": False})
            problems.append(f"{', '.join(empty_sources)} returned no data — anything written from it is unverified")
        if wants_save(state["goal"]):
            wrote = [sid for sid, r in results.items() if r["status"] == "ok" and r.get("tool") in WRITE_TOOLS]
            checks.append({"step": ",".join(wrote) or "-", "check": "artefact written to the vault", "passed": bool(wrote)})
            if not wrote:
                problems.append("the goal asked for something to be saved, but no note was written to the vault")
        passed = not problems
        return {"results": results, "verification": {"passed": passed, "problems": problems, "checks": checks},
                "events": [_ev("verify", t0, passed=passed, checks=checks, problems=problems)]}

    def after_verify(state: AgentState) -> str:
        v, res = state["verification"], state["results"]
        if v["passed"] or state.get("replans", 0) >= s.max_replans:
            return "respond"
        if any(r["status"] in ("rejected", "blocked") for r in res.values()):
            return "respond"  # respect the user's / guardrail's decision; don't route around it
        if any(r["status"] == "failed" and G.is_permanent(r.get("error")) for r in res.values()):
            return "respond"  # broken integration (deleted project, bad key): a new plan hits the same wall
        return "replan"

    async def replan(state: AgentState):
        # Only statuses and errors are passed back to the planner, never raw untrusted content.
        ctx = f"Previous plan: {json.dumps([{k: st.get(k) for k in ('id', 'kind', 'tool')} for st in state['plan']['steps']])}\n" \
              f"Results:\n{_status_lines(state['results'])}"
        return {"replans": state.get("replans", 0) + 1, "failure_context": ctx,
                "events": [{"node": "replan", "ts": time.time(), "latency_ms": 0, "reason": state["verification"]["problems"]}]}

    async def respond(state: AgentState):
        t0 = time.perf_counter()
        live(state, stage="respond")
        p = state.get("plan") or {}
        if state.get("error") and not state.get("results"):
            return {"status": "failed", "answer": f"I couldn't complete this: {state['error']}",
                    "events": [_ev("respond", t0, status="failed")]}
        if p and not p.get("steps"):
            return {"status": "success", "answer": tidy_answer(p.get("direct_answer") or "", state["goal"], set()),
                    "verification": {"passed": True, "problems": [], "checks": []},
                    "events": [_ev("respond", t0, status="success", mode="direct")]}
        v = state.get("verification", {})
        status = "success" if v.get("passed") else "failed"
        results, tainted = state["results"], set(state.get("tainted", []))
        artifacts = [r["output"]["path"] for r in results.values() if r["status"] == "ok" and isinstance(r.get("output"), dict)
                     and "verify" in r["output"]]
        last = p["steps"][-1]
        # Fast path: the deliverable is generated text and everything passed -> return it without another LLM call.
        if status == "success" and last["kind"] in ("generate", "crew") and last["id"] in results:
            answer = output_text(results[last["id"]]["output"])
            answer = tidy_answer(answer, state["goal"], run_urls(results))
            if artifacts:
                answer += "\n\n---\nSaved: " + ", ".join(f"`{a}`" for a in artifacts)
            return {"status": status, "answer": answer, "events": [_ev("respond", t0, status=status, mode="passthrough")]}
        detail = []
        for sid, r in results.items():
            if r["status"] == "ok":
                txt, _ = _render("{{" + sid + "}}", results, tainted, wrap=True)
                detail.append(f"- {sid} [ok] {r['description']}: {txt[:1200]}")
            else:
                detail.append(f"- {sid} [{r['status']}] {r['description']}: {r.get('error', '')}")
        try:
            if not budget_ok(state):
                raise LLMError("LLM call budget exhausted")
            r = await llm_chat(llm, [
                {"role": "system", "content": now_line() + "\nYou are Synapse. Use clean Markdown: tables for structured data, \"- [ ]\" checkboxes for to-dos, "
                     "## headings for sections. Report the outcome to the user in concise Markdown. Only claim what the "
                                              "step results show; if something failed, was rejected or blocked, say so plainly and why. "
                                              "Mention saved note paths. " + G.UNTRUSTED_SYSTEM_NOTE},
                {"role": "user", "content": f"Goal: {state['goal']}\nOutcome: {status}\nProblems: {v.get('problems')}\n"
                                            f"Saved notes: {artifacts}\nStep results:\n" + "\n".join(detail)}],
                on_token=streamer(live, state, stage="respond", final=True))
            answer, calls = tidy_answer(r.text, state["goal"], run_urls(results)), [r]
        except LLMError as e:  # deterministic fallback: never lose the outcome
            answer, calls = f"**Status: {status}**\n\n{_status_lines(results)}\n\n_(summary unavailable: {e})_", []
        return {"status": status, "answer": answer, "llm_calls": state.get("llm_calls", 0) + len(calls),
                "events": [_ev("respond", t0, calls, status=status)]}

    async def memorize(state: AgentState):
        t0 = time.perf_counter()
        live(state, stage="memorize")
        results = state.get("results") or {}
        artifacts = [r["output"]["path"] for r in results.values() if r.get("status") == "ok" and isinstance(r.get("output"), dict)
                     and "verify" in r["output"]]
        memory.add_episode(state["run_id"], state["goal"], state.get("status", "failed"), (state.get("answer") or "")[:500], artifacts)
        stored, calls = [], []
        # Semantic memory: only durable facts the user stated about themselves. Cheap gate avoids an LLM call on most goals.
        if state.get("status") == "success" and FIRST_PERSON.search(state["goal"]) and budget_ok(state):
            try:
                f, calls = await structured(llm, [
                    {"role": "system", "content": "Extract durable facts the USER explicitly states about themselves in their message: "
                                                  "preferences, background, ongoing projects, goals. If they give their name, store it "
                                                  "exactly as {\"text\": \"Name: <their name>\", \"kind\": \"profile\"}. "
                                                  "Ignore the task request itself and "
                                                  "anything temporary. Never extract secrets, passwords, health, financial or ID data. "
                                                  "Return an empty list if there is nothing durable."},
                    {"role": "user", "content": state["goal"]}], Facts)
                for fact in f.facts[:5]:
                    text = fact.get("text", "")
                    if text and not G.SECRET.search(text):
                        r = await memory.add_fact(text, fact.get("kind", "profile"), state["run_id"])
                        if r["stored"]:
                            stored.append(text)
            except LLMError:
                pass  # memory is best-effort; never fail a run because of it
        return {"events": [_ev("memorize", t0, calls, episode=True, facts_stored=stored)]}

    def route_plan(state):
        if state.get("status") == "failed":
            return "respond"
        return "step" if state["plan"]["steps"] else "respond"

    def route_step(state):
        """Continue only while the last EXECUTED step succeeded. The cursor can jump by more than one
        (parallel batch), and a failure can leave later steps unexecuted, so look up by membership."""
        steps, i, results = state["plan"]["steps"], state["cursor"], state["results"]
        done = [st["id"] for st in steps[:i] if st["id"] in results]
        if i >= len(steps) or not done or results[done[-1]]["status"] != "ok":
            return "verify"
        return "step"

    g = StateGraph(AgentState)
    for name, fn in [("recall", recall), ("triage", triage), ("plan", plan), ("step", step), ("verify", verify), ("replan", replan),
                     ("respond", respond), ("memorize", memorize)]:
        g.add_node(name, fn)
    g.add_edge(START, "recall")
    g.add_edge("recall", "triage")
    g.add_conditional_edges("triage", lambda st: "memorize" if st.get("answer") else "plan", ["memorize", "plan"])
    g.add_conditional_edges("plan", route_plan, ["step", "respond"])
    g.add_conditional_edges("step", route_step, ["step", "verify"])
    g.add_conditional_edges("verify", after_verify, ["respond", "replan"])
    g.add_edge("replan", "plan")
    g.add_edge("respond", "memorize")
    g.add_edge("memorize", END)
    return g.compile(checkpointer=checkpointer)
