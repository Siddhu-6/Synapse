# Synapse

A personal agentic assistant that takes a goal, plans it, executes it through MCP tools and specialist agents, verifies what it did, asks before anything risky, and keeps what matters in an Obsidian vault.

Local-first: the default setup runs entirely on your machine with Ollama. No API key, no data leaving the laptop.

```
goal ─► recall ─► plan ─► step ─► verify ──passed──► respond ─► memorize
       (memory)   (LLM)    │       (independent                 (facts +
                           │        read-back)                   episode)
                    approval gate                │ failed & replans < max
                    (LangGraph interrupt)        └────► replan ──┘
```

## What it does

- **Answers small talk instantly.** A cheap triage call routes greetings and general questions straight to an
  answer — no plan, no tools. Anything about the *current* state of the world is routed to research instead,
  because the model's training data is stale.
- **Plans then executes.** The plan is fixed from your goal before any external content is read, so retrieved text cannot inject new tool calls.
- **Runs tools over MCP.** Four stdio servers (vault, tasks, research, google) discovered at startup; every call is schema-validated, timed out, risk-classified and logged.
- **Uses a CrewAI crew where it earns its place.** Analyst → Writer → Critic turns gathered sources into a report. Everything else is plain LangGraph.
- **Asks before risky actions.** Deleting, overwriting, emailing and calendar writes pause the graph with a real `interrupt()`; the run is checkpointed and survives a restart.
- **Proves its writes.** Write tools return a verification recipe; the verifier re-reads the note and compares hashes.
- **Remembers, selectively.** Durable facts about you plus one episode per run, with provenance. Research goes to the vault, not to memory.
- **Never fakes completion.** A goal that asks to save something is rejected at planning time unless the plan
  calls a real vault write tool, and fails verification unless a file actually landed. Text-only "saves" are impossible.
- **Repairs itself cheaply.** A bad tool argument costs one small correction call, not a whole replan — and repaired
  arguments never inherit an approval you gave for different ones.
- **Shows its work live.** A black-and-grey dashboard (light and dark modes, an always-dark sidebar, one sky-green accent for anything live): every exchange is a numbered entry on one line (the axon),
  with its plan, approvals and answer; the margin shows the pipeline and per-span trace with token counts. Agents,
  vault graph, memory, tools and eval results each have a view. Progress streams *during* long nodes. ⌘K searches
  every conversation and entry; ⌘J starts a new one.

## Status

| Area | State |
|---|---|
| LangGraph runtime (plan → execute → verify → bounded replan → respond → memorize) | Working, tested |
| MCP boundary: schema validation, timeouts, risk policy, read-only retries | Working, tested |
| Vault tools: create / append / update / read / search / gather / list / delete | Working, tested |
| Organiser tools: move, replace_section, add_tags, related_notes, vault_stats, list_folders | Working, tested |
| Keyless web search (ddgs with an HTML fallback) | Working, verified live |
| Fast-provider routing with local fallback | Working, tested |
| Parallel independent reads | Working, tested |
| Write verification by read-back and hash; optimistic concurrency on updates | Working, tested |
| Human-in-the-loop approvals (CLI + HTTP), resumable after restart | Working, tested |
| Guardrails: injection scan, taint tracking, recipient policy, budgets | Working, tested |
| Memory: semantic facts + episodes, embeddings with keyword fallback | Working, tested |
| Traces, metrics, run history | Working, tested |
| Dashboard (React, live SSE, Agents view) | Working; builds and served by the API |
| Eval suite, 16 scenarios | Working, 16/16 pass |
| Browser checks (console, a11y, keyboard, responsive, main-thread) | Working, 17/17 pass |
| Notes addressable by bare name (`delete Lionel Messi`) | Working, tested |
| Command palette (⌘K), skip link, ARIA tabs, focus management | Working, browser-verified |
| Plan honesty checks (a save goal must write a real file) | Working, tested |
| Argument repair on schema violations | Working, tested |
| CrewAI research crew | Working; verified live against Ollama |
| Web research (DuckDuckGo + fetch) | Working; needs network, disabled in tests |
| Chat threading: follow-ups in one conversation, new-chat button | Working, tested |
| Markdown rendering: tables, checkboxes, code, headings | Working |
| Notion: search, read, create page, append, add database row | Implemented, **not yet verified live** (needs your token) |
| n8n workflows (WhatsApp/Slack/SMS via webhook allowlist) + desktop notifications | Working, tested offline |
| Gmail / Calendar tools | Implemented, **not yet verified live** (needs your Google OAuth client) |
| Docker, CI | Written, **not built here** (no Docker in the dev sandbox); CI steps all pass locally |

Tests and offline evals replace **only the model** with a scripted stand-in. The graph, MCP servers, guardrails, checkpointer, memory, vault and HTTP API are real in every test.

## Making it fast

A local 7B plans in 30–70s and writes each section in ~40s, so a five-step run takes minutes. Three levers, in
order of effect:

1. **Point it at a free hosted model.** Set `SYNAPSE_FAST_BASE_URL`, `SYNAPSE_FAST_MODEL` and `SYNAPSE_FAST_API_KEY`
   (Groq and Cerebras both have free tiers). Planning and writing drop to a few seconds, and quality improves.
   The local model stays as an automatic fallback, so a spent quota degrades instead of failing.
2. **Keep plans short.** The planner is capped at 6 steps, told to aim for 2–3, allowed one research step, and
   limited to 700 tokens of plan JSON. Step descriptions are capped at 10 words.
3. **Parallel reads.** Consecutive independent read-only steps run concurrently (`SYNAPSE_PARALLEL_STEPS`), and the
   model stays resident in Ollama (`keep_alive=30m`) so calls don't pay a reload.

The crew is two agents by default; the critic is opt-in (`SYNAPSE_CREW_CRITIC=true`) because it roughly doubles
crew latency.

## Quick start (macOS)

Full step-by-step: **[SETUP.md](SETUP.md)**.

```bash
brew install uv node ollama
ollama serve                                 # separate terminal
ollama pull qwen2.5:7b-instruct

uv sync --extra dev --extra google           # Python 3.12 + pinned deps (add --extra crew for CrewAI)
cp .env.example .env                         # point SYNAPSE_VAULT_PATH at your vault
uv run synapse doctor                        # checks Ollama, model, MCP servers, vault path

uv run pytest -q                             # tests
uv run python -m evals.run                   # 16 behaviour scenarios

cd web && npm install && npm run build && cd ..
uv run synapse-api                           # http://127.0.0.1:8000
```

CLI instead of the dashboard:

```bash
uv run synapse run "Compare LangGraph and CrewAI, then save the comparison to Research/"
```

Integrations (Groq, Gmail/Calendar, Notion, n8n for WhatsApp/Telegram/Slack), every setting, GitHub and
deployment are covered step by step in the **[guide (PDF)](Synapse-Deployment-Guide.pdf)**.

## Docs

- [Architecture](docs/ARCHITECTURE.md) — graph, state, MCP boundary, memory, traces
- **[Setup, Integrations & Deployment Guide (PDF)](Synapse-Deployment-Guide.pdf)** — the full manual
- [Deploy](DEPLOY.md) — always-on Mac, Docker, or a public URL
- [Capabilities](docs/CAPABILITIES.md) — what to actually ask it for
- [Tools](docs/TOOLS.md) — every tool, its schema, risk level and failure modes
- [Guardrails](docs/GUARDRAILS.md) — what is protected, and what is not
- [Evaluation](docs/EVALUATION.md) — methodology and how to run it
- [Setup](SETUP.md) — environment, Google/Notion/n8n, Docker
- [Demo scenarios](docs/DEMO.md) — what to show in an interview
- [Limitations](docs/LIMITATIONS.md) — known gaps, honestly

## Layout

```
synapse/            runtime (graph, tools, memory, guardrails, tracing, api, cli)
synapse/mcp_servers vault, tasks, research, google — separate processes over stdio
web/                React dashboard (Vite, Tailwind, React Flow)
evals/              scenario suite + runner
tests/              pytest: runtime, tools, api
```
