# Synapse

> A personal AI assistant that plans, researches, writes to your Obsidian vault, reads your email,
> and politely asks before doing anything it can't take back.

Synapse is not a chatbot with a to-do list glued on. It's an agent system: every request is understood,
planned, executed through real tools, checked, and remembered. You can watch all of it happen live.

Think of it as an intern who never sleeps, always shows their work, and physically cannot send an email
without your signature.

![Synapse dashboard](docs/screenshot.png)

---

## What it does

- **Answers fast when it already knows.** "What is RAG?" takes about 3 seconds. No committee meeting required.
- **Researches when it doesn't.** It reads real pages and keeps only links it actually opened. The made-up ones get deleted before you see them.
- **Keeps your vault tidy.** "Add this to Sid's note" updates the existing note. It will not create "Sid's Personality (2).md". We've been through this.
- **Reads your email properly.** "Did Vamshi reply?" reads the whole thread. (He said "Hello brother". Riveting stuff.)
- **Calls in specialists.** Type `/study`, `/decide`, `/research`, `/review` or `/notes` and a small team of agents gets to work.

## How it works

```
You --> Triage --> "I know this" ------------------------------------> Answer
          |
          +--> Plan --> Steps (tools, writer, crews) --> Verify --> Respond --> Memory
                 ^                  |                       |
                 +--- replan (bounded) <--- failed ---------+
                                    |
                           risky? --> Human approval
```

- **LangGraph** runs the show: a checkpointed state machine that survives crashes and waits patiently for your approval.
- **MCP** is the bouncer. Every capability (vault, web, Gmail, Calendar, Notion, tasks, n8n) goes through a typed, time-limited, risk-classed tool boundary.
- **CrewAI** supplies specialist teams for the jobs where several opinions beat one prompt.
- **Memory** comes in three flavours: past runs, facts you told it, and your Obsidian vault as long-term knowledge.

### The crews

| Command | Crew | Team |
|---|---|---|
| `/research` | Research | researcher, analyst, writer |
| `/study` | Study | curriculum designer, tutor, quizmaster |
| `/review` | Editor | critic, editor |
| `/notes` | Archivist | archivist, synthesiser |
| `/decide` | Decision | advocate, skeptic, judge (mostly civil) |

Crews can look things up but never touch anything. Writing, sending and deleting stay in the main graph,
where approvals and guardrails live. Nobody gets to freelance.

## Guardrails

Otherwise known as the "please don't email my professor at 3am" layer.

- **The plan is locked in first.** Synapse decides what to do before it reads any web page or email, so text hidden inside them can't add new actions.
- **Suspicious sources are tracked.** Anything that came from the web or someone else's email stays marked as untrusted all the way through.
- **Risky actions need your signature.** Sending, deleting and overwriting always wait for you.
- **Strangers stay off the recipient list.** It won't email an address you never gave it.
- **Hard budgets on everything.** Steps, replans, model calls and crew tool calls are all capped, so there are no infinite loops and no surprise bills.

Guardrails reduce risk. They do not turn a language model into a lawyer.
See [`docs/GUARDRAILS.md`](docs/GUARDRAILS.md).

## Observability and evals

- **Every run is traced:** model, latency, tokens, tool calls, retries, approvals, and why it fell back to another model if it did.
- **The dashboard shows it all live:** the pipeline, agents lighting up as they work, answers streaming in, your vault as a graph, and memory.
- **Behaviour evals:** `python -m evals.run` checks tool choice, arguments, approvals, recovery and prompt injection against the real graph.
- **78 tests,** which test behaviour, not whether `import` works.

## Quick start

```bash
git clone https://github.com/<your-username>/synapse.git && cd synapse
uv venv && uv pip install -e ".[crew,google,dev]"
cp .env.example .env            # set SYNAPSE_VAULT_PATH, and ideally a free Groq key
ollama pull qwen2.5:7b-instruct # optional local fallback
uv run synapse-api              # then open http://127.0.0.1:8000
```

Want it fast and free? Put a free [Groq](https://console.groq.com/keys) key in `.env` and
`openai/gpt-oss-120b` answers in 1 to 3 seconds. You can switch models any time from the button next to the
message box.

Gmail and Calendar, Notion and n8n are optional. `SETUP.md` has the details.

## Stack

Python, LangGraph, CrewAI, MCP, FastAPI, SQLite, React + Vite + Tailwind, Ollama, Groq, Obsidian

---

Built by **Siddhikesh Gavit**, final-year CSE at IIIT Vadodara.

If it ever sends an email you didn't approve, that's a bug.
If it signs the email "Best regards, Synapse", that's also a bug.
