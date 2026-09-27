# Evaluation

```bash
python -m evals.run                 # offline, deterministic — 16 scenarios
python -m evals.run --only guard    # filter by id or category
python -m evals.run --live          # same shapes against the real model
```

The report lands in `data/evals/latest.json` and is rendered in the dashboard's Evals tab. Exit code is non-zero on any failure, so CI fails when behaviour regresses.

## Method

Each scenario gives the runtime a goal and a scripted sequence of model replies, then asserts on **what the system actually did**: files on disk, tool calls recorded in spans, approval prompts raised, replan counts, stored memory, final status.

Only the model is scripted. The graph, MCP subprocesses, guardrails, approval interrupts, checkpointer, memory and vault are real, in a fresh temp vault per scenario. This makes the suite deterministic and fast (~18s for 16 scenarios) while still testing the parts that break in production. A few scenarios are pure unit checks of guardrail functions, marked `static`.

Scripting the model is a deliberate trade: it tests the **system**, not the model's intelligence. Model quality is checked separately with `--live`, which asserts structural properties (valid plan, tools that exist, run completes) rather than exact text.

## Coverage

| Category | Scenarios | Asserts |
|---|---|---|
| goal completion | 1 | note written, verified by read-back, answer references it |
| tool selection | 1 | read-only tool chosen, no write tools, no approval prompt |
| tool parameters | 2 | arguments passed through; schema violation caught pre-execution, then repaired |
| human-in-the-loop | 3 | approval requested; action only after approval; rejection leaves data untouched; unattended runs reject risky actions |
| prompt injection | 2 | injected instructions never become tool calls; patterns detected; delimiters unspoofable; taint forces approval |
| guardrails | 2 | unknown recipients blocked; unknown tools rejected and repaired; overwrite escalated to high risk |
| failure recovery | 2 | bounded replan recovers via a different path; user data never overwritten; exhausted replans reported as failure |
| memory | 2 | durable facts stored; recall runs before planning |
| latency/reliability | 1 | framework overhead budget |

## Honest notes

- Live Gmail and Calendar behaviour is **not** covered; those tools need credentials and are excluded from CI.
- Web research is disabled in evals for determinism; it is exercised manually.
- There is no LLM-as-judge scoring of answer quality. It would add a dependency on the judge's reliability for little signal at this scale.
