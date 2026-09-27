# Architecture

## Why this shape

The core decision is **plan-then-execute** rather than a free-running tool loop. The plan is produced from the user's goal *before* any external content enters the context. Anything the agent reads afterwards is data, and cannot add a step. That single choice removes most of the prompt-injection surface and makes runs cheap to verify, resume and evaluate. The cost is less adaptivity, which is paid back through bounded replanning.

## Runtime (LangGraph)

Nodes, in order:

| Node | What it does | LLM |
|---|---|---|
| `recall` | pulls relevant facts and past episodes from memory | no |
| `plan` | one structured call → validated `Plan`; one repair round if invalid | yes |
| `step` | executes one step (tool / generate / crew), loops via a conditional edge | sometimes |
| `verify` | deterministic checks: step statuses, empty content, write read-back | no |
| `replan` | bounded retry with a sanitised failure summary | no |
| `respond` | writes the user-facing answer, with a deterministic fallback | yes |
| `memorize` | extracts durable facts, stores one episode | yes (only if the goal is personal) |

`step` runs **one** step per node invocation and keeps a `cursor` in state. That matters for human-in-the-loop: `interrupt()` replays the node it paused in, so a node that executed several tools would re-execute them on resume. One step per invocation makes resumption idempotent.

State (`AgentState`) is a `TypedDict`: goal, memory, plan, cursor, results, tainted step ids, verification, replans, llm_calls, status, answer, and two append-only channels (`events`, `approvals`).

Persistence is `AsyncSqliteSaver` with `thread_id = run_id`. A run paused for approval survives an API restart and resumes from the checkpoint.

## Honesty checks

Two layers stop the worst failure mode, a run that reports success having done nothing:

1. **Plan validation.** A `generate` or `crew` step whose description promises an action ("Create a note…", "Save…")
   is rejected — those step kinds only produce text. If the goal asks to save anything, the plan must contain
   `create_note`, `append_to_note` or `update_note`. The planner receives the rejection and corrects itself.
2. **Artefact verification.** For a save goal, `verify` requires at least one successful write tool. No file, no success.

This was found in real use: a 7B model planned five steps, all `generate`, including "Create a markdown note", and the
run reported success with an empty vault.

## Argument repair

When a tool call fails on its schema (missing or misnamed argument), the executor makes one small correction call with
the tool schema and the error, then retries once. This is much cheaper than replanning the whole goal, which is what
local models otherwise force you into. Repair is only attempted for calls that ran without approval: repaired arguments
must never ride on an approval the user gave for different ones.

## Performance notes

- `keep_alive=30m` keeps the model resident in Ollama; without it, each call may pay a reload.
- `num_predict` caps planning and generation separately; the planner's JSON does not need 1,200 tokens.
- The tool catalog sent to the planner is compact (name, params, risk, one short line) — about 2.5KB for 14 tools.
- The crew is two agents by default (analyst, writer). The critic roughly doubles crew latency and is opt-in via
  `SYNAPSE_CREW_CRITIC=true`.
- Steps run sequentially. On an M-series Mac with a 7B model, expect roughly 30–60s for a note-writing goal and
  several minutes for a crew research report.

## Plan contract

```python
Step:  id, kind: tool|generate|crew, description, tool, args, prompt
Plan:  intent, deliverable, reasoning, steps[], direct_answer
```

Steps pass data with `{{s1}}` (whole output) or `{{s1.sha256}}` (one field). Field references exist because models invent hashes: `update_note` requires `expected_sha256` from a real `read_note`, giving optimistic concurrency on note edits.

`validate_plan` rejects unknown tools, duplicate ids, forward references, missing prompts and over-long plans before anything executes; the planner gets the errors back and retries once.

## MCP boundary

Each server is a separate process speaking stdio MCP. `Toolbox` discovers tools at startup and wraps every call with:

- JSON-Schema validation of arguments (client side, before the call)
- a timeout (`SYNAPSE_TOOL_TIMEOUT`, default 30s)
- a **client-side** risk policy — server-declared annotations are hints from the other side of the boundary, so they are not trusted; unknown tools default to `high`
- retries for read-only tools only, because retrying a write after a timeout could apply it twice
- a typed `ToolResult` (`ok`, `data`, `error`, `latency_ms`, `attempts`) — a failing tool never raises into the graph

A server that fails to start is recorded as `down` and the rest keep working.

## CrewAI

One use: turning gathered sources into a report (Analyst → Writer → Critic, sequential). Sources are fetched beforehand through MCP, so the crew has no tool access and a small attack surface. It runs in a worker thread with a timeout, and a crew failure degrades to a normal `generate` step rather than failing the run.

## Memory

- **Working** — LangGraph state, checkpointed per run.
- **Episodic** — one row per run: goal, status, summary, artifacts written.
- **Semantic** — durable facts about the user with `kind` (preference / profile / project), source and run id. Deduplicated by exact match and by cosine similarity above 0.92.

Extraction only runs when the goal is written in the first person, so research runs don't pollute memory with facts about the world. Embeddings come from Ollama (`nomic-embed-text`); if the model isn't pulled, the embedder disables itself after the first failure and retrieval falls back to keyword overlap.

## Observability

Every node emits a span: name, timestamp, latency, plus attributes (tool call with arguments, risk, approvals, injection flags, per-call model and token counts). Spans are stored in SQLite with OpenTelemetry-shaped fields (`trace_id`, `span_id`) so an OTLP exporter to Langfuse or similar is a small addition rather than a rewrite. Secrets are redacted on write. `/api/metrics` derives p50/p95 latency, tool-error counts and run outcomes from the same table.

## API and dashboard

FastAPI owns one `Runtime` for the process: MCP servers, checkpointer, memory, traces. Runs execute as background tasks and publish to per-run queues; the dashboard consumes them over SSE. If `SYNAPSE_API_TOKEN` is set, every `/api` route requires it except `/api/health`, which stays open for container probes. The built dashboard is served by the same process, so deployment is a single container.
