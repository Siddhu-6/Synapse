# Synapse

> A personal agentic assistant that takes a goal, plans it, executes it through MCP tools and specialist agents,
> verifies what it did, asks before anything risky, and keeps what matters in an Obsidian vault.

Synapse is not a chatbot that just "**answers**", but a multi-agent system that "**executes**". Every request is understood, planned, handed to the right agent, executed through real tools, checked, and remembered, and the dashboard shows each step happening live.

Small talk gets a quick answer. Real work gets a plan, a team, and reliable execution. Anything irreversible with high risks waits for your approval, because autonomy without accountability is just a very confident bug.


![Synapse dashboard](docs/screenshot.png)

---

## What it does

- **Answers and explains.** Questions it already knows get a direct answer in seconds. Anything current gets researched on the web, with links it actually opened.
- **Runs your knowledge base.** Creates, updates, merges, links, tags, organises and deletes notes in your Obsidian vault, and answers questions from them.
- **Handles email and calendar.** Searches and reads Gmail conversations, drafts and sends emails, lists your schedule and creates events.
- **Sends messages anywhere.** WhatsApp, Slack, SMS or anything else you've wired into n8n, plus desktop notifications.
- **Manages tasks and schedules.** Adds, lists and completes tasks, and runs goals on a timer ("every weekday at 8:30, brief me").
- **Works with Notion.** Searches and reads pages, creates new pages, and adds rows to databases.
- **Knows the everyday stuff.** Weather, the current date and time, and what it has learned about you.
- **Handles inbound messages.** An incoming WhatsApp or SMS gets a routine reply automatically, or you get pinged if it needs a human.
- **Calls in specialists.** Deep research, study guides, decisions, editing and note synthesis are handled by dedicated CrewAI teams.

## How it works

```
You --> Triage --> "I know this" ----------------------------------------> Answer
          |
          +--> Plan --> Steps (agents, tools, crews) --> Verify --> Respond --> Memory
                 ^                  |                       |
                 +--- replan (bounded) <--- failed ---------+
                                    |
                           risky? --> Human approval
```

**LangGraph** is the orchestrator: a checkpointed state machine. Runs survive restarts and pause cleanly while
they wait for your approval. A quick triage step sends simple questions straight to an answer, so there's no
planning theatre for "hi".

## The agents

Each agent owns a set of tools. The dashboard lights them up as work moves through them.

| Agent | Role |
|---|---|
| **Planner** | Understands the goal, builds a validated step-by-step plan, replans when a step fails |
| **Verifier** | Reads every write back and checks the content really landed |
| **Researcher** | Searches the web and reads pages; treats everything it finds as untrusted |
| **Librarian** | Searches the vault and keeps memory: past runs and facts you've shared |
| **Scribe** | Writes answers, notes and drafts, streaming them as they're written |
| **Organiser** | Moves, tags, links, merges and deletes notes, and never duplicates one |
| **Comms** | Gmail, WhatsApp/Slack/SMS through n8n, and desktop notifications |
| **Scheduler** | Calendar, tasks and timed goals |

### CrewAI crews

For jobs where several perspectives beat one prompt. Start a message with the command to call one directly.

| Command | Crew | Team |
|---|---|---|
| `/research` | Research | researcher, analyst, writer |
| `/study` | Study | curriculum designer, tutor, quizmaster |
| `/review` | Editor | critic, editor |
| `/notes` | Archivist | archivist, synthesiser |
| `/decide` | Decision | advocate, skeptic, judge (mostly civil) |

Crews can look things up but never write, send or delete. Those actions stay in the main graph, where
approvals and guardrails apply. Nobody gets to freelance.

## MCP servers

Every capability sits behind the **Model Context Protocol**. Each tool call is schema-checked, time-limited,
risk-classed and logged, 38 tools in total.

| Server | Capabilities |
|---|---|
| **vault** | Obsidian notes: create, read, append, update, merge, move, tag, link, search, delete (to `.trash`) |
| **research** | Web search, page reading, search-and-read research |
| **google** | Gmail search, conversation threads, drafts, sending; Calendar events |
| **notion** | Search, read pages, create pages, add database rows |
| **automation** | n8n workflows (WhatsApp, Slack, SMS, anything), desktop notifications |
| **tasks** | Personal task list and scheduled goals |
| **world** | Weather, current date and time |

## Guardrails

Otherwise known as the "please don't email my professor at 3am" layer.

- **The plan is locked in first.** Synapse decides what to do before it reads any web page or email, so text hidden inside them can't add new actions.
- **Suspicious sources are tracked.** Anything that came from the web or from someone else's email stays marked as untrusted all the way through.
- **Risky actions need your approval.** Sending, deleting and overwriting always wait for you; editing based on untrusted content does too.
- **Only known recipients.** It won't email an address you never gave it.
- **Only real links.** Links it didn't actually open are removed from answers.
- **Hard budgets on everything.** Steps, replans, model calls and crew tool calls are capped, so there are no infinite loops and no surprise bills.

## Observability and evals

- **Every run is traced:** model, latency, tokens, tool calls, retries, approvals, and why it fell back to another model if it did.
- **A live dashboard:** the pipeline, agents lighting up as they work, answers streaming in, your vault as an Obsidian-style graph, memory, tools and eval results.
- **Behaviour evals:** `python -m evals.run` checks tool choice, arguments, approvals, recovery and prompt-injection resistance against the real graph.
- **78 tests,** which test behaviour, not whether `import` works.

## Models

Local-first with **Ollama**, or free and fast with **Groq** (`openai/gpt-oss-120b` answers in 1 to 3 seconds).
Switch models from the dashboard at any time. Each run finishes on the model it started with, and a hosted
model that hits a problem falls back to local automatically.

## Quick start

```bash
git clone https://github.com/<your-username>/synapse.git && cd synapse
uv venv && uv pip install -e ".[crew,google,dev]"
cp .env.example .env            # set SYNAPSE_VAULT_PATH, and ideally a free Groq key
ollama pull qwen2.5:7b-instruct # optional local fallback
uv run synapse-api              # then open http://127.0.0.1:8000
```

Gmail and Calendar, Notion and n8n are optional. `SETUP.md` walks through each.

## Stack

Python, LangGraph, CrewAI, MCP, FastAPI, SQLite, React + Vite + Tailwind, Ollama, Groq, Obsidian, n8n

---

Built by **SIDDHIKESH**...[Synapse-One of the reasons for streak-gap on my profile]
