"""CrewAI crews: small teams of specialist agents for work where several perspectives beat one prompt.

Each crew is a sequential team with a clear hand-off, and some agents get TOOLS: read-only MCP tools
(web search, page fetch, vault search/read) bridged into CrewAI. Crews never write, send or delete —
those stay as graph steps, where approval and guardrails apply. Tool output is wrapped as untrusted
content, and each crew has a hard budget of tool calls.

  research  Researcher (web) -> Analyst -> Writer [-> Critic]   deep, sourced reports and comparisons
  study     Curriculum designer (web) -> Tutor -> Quizmaster     learn a topic: guide, examples, quiz
  review    Critic -> Editor                                     improve a draft: email, post, resume, essay
  vault     Archivist (vault) -> Synthesiser                     what do my notes say / merge my notes
  decide    Advocate (web) -> Skeptic (web) -> Judge             weigh a decision from both sides
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from . import guardrails as G
from .config import Settings

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# ---------------- tools (read-only MCP tools, bridged into CrewAI) ----------------


# Schemas are lenient on purpose: gpt-oss models sometimes call a tool with the arguments of their own
# built-in browser ({"cursor": 2, "id": 0}); Groq rejects any call that breaks the schema with a 400 and
# the crew stalls. Accepting it and replying "give a query" lets the agent correct itself.
class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Search(_Lenient):
    query: str = Field(default="", description="what to search for, in plain words")


class _Fetch(_Lenient):
    url: str = Field(default="", description="a full http(s) URL taken from search_the_web results")


class _NoteSearch(_Lenient):
    query: str = Field(default="", description="words to look for in the user's notes")


class _NoteRead(_Lenient):
    path: str = Field(default="", description="vault-relative note path, e.g. 'People/Sid.md'")


# crew-facing name -> (schema, description, MCP tool, argument mapping, required field)
TOOL_SPECS = {
    "search_the_web": (_Search, "Search the web. Argument: query (plain words). Returns titles, URLs and snippets.",
                       "web_search", lambda a: {"query": a["query"], "max_results": 6}, "query"),
    "read_web_page": (_Fetch, "Read one web page as text. Argument: url (a full URL from search_the_web).",
                      "fetch_url", lambda a: {"url": a["url"], "max_chars": 5000}, "url"),
    "search_my_notes": (_NoteSearch, "Search the user's Obsidian vault. Argument: query. Returns note paths and snippets.",
                        "search_notes", lambda a: {"query": a["query"], "limit": 8}, "query"),
    "read_my_note": (_NoteRead, "Read one note from the user's vault. Argument: path.",
                     "read_note", lambda a: {"path": a["path"]}, "path"),
}
WEB, VAULT = ["search_the_web", "read_web_page"], ["search_my_notes", "read_my_note"]
MAX_TOOL_CALLS = 10


class Budget:
    def __init__(self, n: int):
        self.left, self.used, self.lock, self.cache = n, [], threading.Lock(), {}

    def take(self, name: str) -> bool:
        with self.lock:
            if self.left <= 0:
                return False
            self.left -= 1
            self.used.append(TOOL_SPECS[name][2] if name in TOOL_SPECS else name)
            return True


def make_tools(names: list[str], call: Callable[[str, dict], dict], budget: Budget, on_agent=None) -> list:
    """CrewAI tools that run through Synapse's MCP Toolbox (schema-checked, time-limited, logged)."""
    from crewai.tools import BaseTool

    tools = []
    for name in names:
        schema, desc, mcp_name, to_args, need = TOOL_SPECS[name]

        class T(BaseTool):
            _call: Callable = PrivateAttr()
            _to_args: Callable = PrivateAttr()
            _mcp: str = PrivateAttr()
            _need: str = PrivateAttr()

            def _run(self, **kw) -> str:
                if not str(kw.get(self._need) or "").strip():
                    return f"{self.name} needs a '{self._need}' argument, e.g. {self.name}({self._need}=\"...\"). Try again."
                key = (self.name, json.dumps(kw, sort_keys=True))
                if key in budget.cache:   # models re-issue the same call in a loop; answer from cache and nudge
                    return budget.cache[key] + "\n\n(You already made this exact call. Do not repeat it — write your answer now.)"
                if not budget.take(self.name):
                    return "STOP: the tool budget is used up. Do not call any more tools. Write your final answer now."
                if on_agent:
                    on_agent({"agent": "tool", "status": "tool", "tool": self._mcp,
                              "detail": str(kw.get("query") or kw.get("url") or kw.get("path") or "")[:80]})
                r = self._call(self._mcp, self._to_args(kw))
                if not r["ok"]:
                    return f"{self.name} failed: {r['error']}"
                text = json.dumps(r["data"], ensure_ascii=False, default=str)[:6000]
                budget.cache[key] = out = G.wrap_untrusted(text, self._mcp)
                return out

        t = T(name=name, description=desc, args_schema=schema)
        t._call, t._to_args, t._mcp, t._need = call, to_args, mcp_name, need
        tools.append(t)
    return tools


# ---------------- crew definitions ----------------


@dataclass
class A:
    key: str
    role: str
    goal: str
    backstory: str
    task: str            # what this agent does; {brief} and {sources} are filled in
    expected: str
    tools: list[str] = field(default_factory=list)
    optional: str | None = None   # a Settings flag that must be true for this agent to run


FACTS = ("Only state what the sources or the brief support. Never invent URLs, quotes, statistics or personal facts. "
         "Copy URLs exactly as they appeared in tool results.")

CREWS: dict[str, dict] = {
    "research": {
        "name": "Research crew", "use": "deep, sourced reports; comparisons of tools/products/options",
        "agents": [
            A("researcher", "Web Researcher", "Find and read the best current sources for the brief",
              "Searches precisely and reads the pages that matter. " + FACTS,
              "Brief: {brief}\n\nSources already gathered (may be empty):\n{sources}\n\nUse search_the_web and read_web_page to "
              "find 3-5 strong, recent sources. Read the most relevant pages. Return compact notes (max 300 words): key "
              "facts, each with its URL.",
              "Research notes with URLs", WEB),
            A("analyst", "Research Analyst", "Turn research notes into accurate, relevant findings",
              "Meticulous; separates evidence from opinion. " + FACTS,
              "From the research notes, extract the findings that answer the brief: {brief}. Group them, note "
              "trade-offs and disagreements between sources. Keep each finding's URL. Max 300 words.",
              "Grouped findings with URLs"),
            A("writer", "Technical Writer", "Write a clear, well-structured Markdown report",
              "Writes for busy engineers: headings, a comparison table when options are compared, no filler. " + FACTS,
              "Write the final Markdown report for: {brief}. Lead with the direct answer, then detail, then a "
              "'Sources' list with the URLs from the findings.", "Final Markdown report"),
            A("critic", "Critic", "Remove unsupported claims and fix structure",
              "Skeptical reviewer. " + FACTS,
              "Check the report against the findings. Remove or fix anything unsupported. Output ONLY the final report.",
              "Final Markdown report", optional="crew_critic"),
        ],
    },
    "study": {
        "name": "Study crew", "use": "learning a topic: study guide, learning path, explanations, practice quiz",
        "agents": [
            A("curriculum", "Curriculum Designer", "Design the right learning path for the learner's goal",
              "Designs practical, up-to-date curricula; checks current tools and resources on the web. " + FACTS,
              "Learner's request: {brief}\n\nContext:\n{sources}\n\nIf useful, use search_the_web to check what is current. "
              "Produce an ordered syllabus of 4-8 modules: for each, what to learn, why it matters, and the concrete "
              "tools/libraries involved.", "Ordered syllabus", WEB),
            A("tutor", "Tutor", "Explain each module so a motivated beginner really understands it",
              "Explains with intuition first, then a small concrete example. Plain language.",
              "For each module of the syllabus write a short explanation with one concrete example (code where it helps) "
              "and one mini-exercise.", "Explained modules"),
            A("quizmaster", "Quizmaster", "Check understanding with a short quiz",
              "Writes fair questions that test understanding, not trivia.",
              "Assemble the final Markdown study guide for: {brief}. Include the syllabus and explanations, then a "
              "'Quiz' section with 5-8 questions and an 'Answers' section at the end.", "Final Markdown study guide"),
        ],
    },
    "review": {
        "name": "Editor crew", "use": "improving a draft: email, message, post, resume, cover letter, essay, README",
        "agents": [
            A("critic", "Critic", "Find what weakens the draft",
              "Sharp, specific, kind. Judges clarity, tone, structure, correctness and fit for the audience.",
              "Request: {brief}\n\nDraft:\n{sources}\n\nList the concrete problems (clarity, tone, structure, errors, "
              "missing points), most important first.", "Prioritised critique"),
            A("editor", "Editor", "Produce the improved version",
              "Rewrites to fix every point without changing facts or adding new claims. Keeps the author's voice.",
              "Rewrite the draft fixing the critique. Output the improved text first, then a short '## What changed' "
              "list.", "Improved text + what changed"),
        ],
    },
    "vault": {
        "name": "Archivist crew", "use": "what do my notes say about X; summarise or merge the user's own notes",
        "agents": [
            A("archivist", "Archivist", "Find every note in the vault that is relevant to the request",
              "Knows the vault; searches with several phrasings and reads the notes it finds. Never invents notes.",
              "Request: {brief}\n\nNotes Synapse already found for this request:\n{sources}\n\nThese are usually enough. "
              "Only if something is clearly missing, use search_my_notes / read_my_note (each note at most once). "
              "Return each relevant note's path and its full relevant content.",
              "Relevant notes with paths", VAULT),
            A("synthesiser", "Synthesiser", "Answer from the user's notes, or merge them into one",
              "Combines notes faithfully: keeps every fact, removes duplicates, never adds facts the notes lack.",
              "Answer the request from the collected notes: {brief}. If it asks to merge or combine, output ONE merged "
              "Markdown note. Link source notes as [[Note Name]]. Say plainly if the notes do not contain the answer.",
              "Answer or merged note in Markdown"),
        ],
    },
    "decide": {
        "name": "Decision crew", "use": "weighing a choice: X vs Y, should I ..., which option",
        "agents": [
            A("advocate", "Advocate", "Make the strongest honest case FOR each option",
              "Argues each option at its best, with evidence. " + FACTS,
              "Decision: {brief}\n\nContext:\n{sources}\n\nIdentify the options. Use search_the_web if facts would help. "
              "Give the strongest case for each option, at most 120 words per option, with source URLs.",
              "Case for each option (short)", WEB[:1]),
            A("skeptic", "Skeptic", "Find the risks, costs and hidden downsides of each option",
              "Stress-tests every option, including the one that looks best. " + FACTS,
              "Using the Advocate's findings, challenge each option: risks, costs, failure modes, what would have to be "
              "true for it to be a mistake. At most 120 words per option.", "Risks for each option (short)"),
            A("judge", "Judge", "Recommend a decision the user can act on",
              "Weighs both sides fairly and commits to a recommendation, with the conditions that would change it.",
              "Write the final Markdown: a recommendation first, then a comparison table (option, for, against), "
              "then 'Choose differently if ...'.", "Recommendation in Markdown"),
        ],
    },
}


def crew_catalog() -> str:
    """One line per crew, for the planner prompt."""
    return "; ".join(f"{k} = {v['use']}" for k, v in CREWS.items())


# ---------------- running ----------------


def _llm(s: Settings, spec: dict | None = None):
    """The crew uses the same model as the rest of the run (the one picked in the dashboard)."""
    from crewai import LLM
    spec = spec or ({"provider": "ollama", "model": s.model} if s.llm_provider == "ollama" else
                    {"provider": "openai", "model": s.model, "base_url": s.openai_base_url,
                     "api_key": s.openai_api_key.get_secret_value() if s.openai_api_key else None})
    if spec["provider"] == "ollama":
        return LLM(model=f"ollama/{spec['model']}", base_url=s.ollama_url, temperature=0.2, timeout=s.llm_timeout)
    return LLM(model=f"openai/{spec['model']}", base_url=spec["base_url"], api_key=spec["api_key"],
               temperature=0.2, timeout=s.llm_timeout, max_tokens=s.gen_max_tokens + 1024)


def _run(s: Settings, kind: str, brief: str, sources: str, on_agent=None, spec: dict | None = None,
         call: Callable | None = None) -> dict:
    from crewai import Agent, Crew, Process, Task

    crew = CREWS[kind]
    members = [a for a in crew["agents"] if not a.optional or getattr(s, a.optional, False)]
    llm = _llm(s, spec)
    budget = Budget(MAX_TOOL_CALLS)
    agents, tasks = [], []
    for m in members:
        tools = make_tools(m.tools, call, budget, on_agent) if (m.tools and call) else []
        agent = Agent(role=m.role, goal=m.goal, backstory=m.backstory + " " + G.UNTRUSTED_SYSTEM_NOTE, llm=llm,
                      tools=tools, allow_delegation=False, verbose=False, max_iter=6 if tools else 2,
                      max_rpm=20, inject_date=True, max_execution_time=int(s.crew_timeout))
        agents.append(agent)
        tasks.append(Task(description=m.task.format(brief=brief, sources=sources or "(none)"),
                          expected_output=m.expected, agent=agent, context=list(tasks[-2:])))

    def task_done(output):                 # streams "this agent finished" to the dashboard mid-run
        if on_agent:
            role = getattr(getattr(output, "agent", None), "role", None) or str(getattr(output, "agent", ""))
            on_agent({"agent": role or "crew", "status": "done", "chars": len(str(output))})

    if on_agent:
        on_agent({"agent": agents[0].role, "status": "running", "crew": kind})
    out = Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=False, task_callback=task_done).kickoff()
    usage = getattr(out, "token_usage", None)
    report = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", str(out.raw).strip())
    return {"report": report, "crew": kind, "agents": [a.role for a in agents], "tools_used": budget.used,
            "tokens": getattr(usage, "total_tokens", None)}


async def run_crew(s: Settings, kind: str, brief: str, sources: str, on_agent=None, spec: dict | None = None,
                   toolbox=None) -> dict:
    """Run a crew in a worker thread. Its tools call back into the MCP Toolbox on this event loop."""
    if kind not in CREWS:
        raise ValueError(f"unknown crew {kind!r}; choose one of {sorted(CREWS)}")
    loop = asyncio.get_running_loop()
    call = None
    if toolbox is not None:
        def call(name: str, args: dict) -> dict:
            if name not in G.READ_ONLY:               # crews may only read; writes stay in the graph
                return {"ok": False, "error": "crews may only use read-only tools", "data": None}
            fut = asyncio.run_coroutine_threadsafe(toolbox.call(name, args), loop)
            r = fut.result(timeout=s.tool_timeout + 5)
            return {"ok": r.ok, "error": r.error, "data": r.data}
    return await asyncio.wait_for(asyncio.to_thread(_run, s, kind, brief, sources, on_agent, spec, call), s.crew_timeout)


async def run_research_crew(s: Settings, brief: str, sources: str, on_agent=None, spec: dict | None = None) -> dict:
    """Backward-compatible entry point: the original analyst/writer research crew."""
    return await run_crew(s, "research", brief, sources, on_agent, spec)
