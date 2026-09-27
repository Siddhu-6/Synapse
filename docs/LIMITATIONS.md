# Known limitations

Written so nothing here is a surprise in a code review.

## Not verified live

- **Gmail and Calendar tools.** Code is complete and risk-classified, but has never run against a real Google account here; it needs your OAuth client. Treat as implemented-untested until you run it.
- **Hosted OpenAI-compatible provider.** Implemented and used by the config path, but every live run so far has been Ollama.

## Design limits

- **Plan-then-execute is less adaptive** than a tool-calling loop. Mid-plan discoveries only change things through the replan path, which is bounded at one retry by default.
- **Sequential execution.** Steps run one at a time; independent research steps are not parallelised. Simpler to verify and resume, slower on multi-source research.
- **Single user.** One bearer token, no roles, no per-user data separation, no rate limiting.
- **Memory extraction is heuristic.** Facts are only pulled from first-person goals, so something durable stated in passing can be missed; a wrong fact is easy to remove in the Memory tab.
- **The vault graph is wikilink-only.** Tag and folder relationships are not drawn, and the layout is a small hand-rolled force simulation rather than a full graph engine.
- **Verification is structural, not semantic.** It proves a write landed with the exact bytes intended; it does not check that a summary is accurate. Factual accuracy relies on the crew's critic step and on the sources being in context.

## Speed

Local generation dominates every run. A 7B model on an M4 plans in roughly 15–40s; each `generate` step is similar;
a crew report is minutes. The framework itself adds well under a second per run (asserted in the eval suite).
If you need faster, the honest options are a smaller model for `generate` steps, a hosted provider, or fewer steps —
not more framework.

## Operational

- **Local model latency.** On an M4 with a 7B model, a research run with a crew step takes minutes, dominated by generation. The CPU-only sandbox used during development was far slower still.
- **`mcp` is pinned below 2.0** because CrewAI requires `mcp~=1.28`. The 2.x API renamed `FastMCP` to `MCPServer` among other changes; migrating means dropping CrewAI or waiting for it to catch up.
- **SQLite everywhere.** Fine for one user on one machine; it would need Postgres for concurrent users.
- **No log rotation.** Trace and checkpoint databases grow unbounded; delete `data/*.db` to reset.

## Hosted models (Groq free tier)
- Groq's free tier allows about 8,000 tokens per minute **per model**; one Synapse run uses roughly 6–7k
  (the planner prompt carries every tool schema). Asking twice within a minute hits the limit. Synapse waits
  out a short limit (up to 20s, the wait Groq reports) and retries on Groq; a longer one falls back to the
  local Ollama model. The trace records `fallback_from` with the reason whenever that happens.
- Switching models in the dashboard applies from the next request. A run always finishes on the model it
  started with. The choice is saved in `data/model.json`.
- For recommendation questions the writer may name well-known tools beyond what the fetched pages say.
  Those names come from the model's own knowledge and can occasionally be wrong or outdated.
