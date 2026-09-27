# Synapse — setup guide

Everything below has been run end to end. Where something is unverified, it says so.

---

## 0. What you need

| | Why | Install |
|---|---|---|
| macOS or Linux | tested on macOS (Apple Silicon) | — |
| Python 3.11+ | the runtime | `brew install python@3.12` |
| uv | fast venv + installs | `brew install uv` |
| Node 20+ | builds the dashboard once | `brew install node` |
| Ollama | local model | `brew install ollama` |
| Obsidian (optional) | to see the vault | `brew install --cask obsidian` |

A free Groq or Cerebras key is strongly recommended. A local 7B plans in 30–70s; a hosted model does it in
2–5s and plans better. Local stays as the automatic fallback.

---

## 1. Model

Terminal 1 (leave it running):

```bash
ollama serve
```

Terminal 2:

```bash
ollama pull qwen2.5:7b-instruct     # ~4.7GB, the planner
ollama pull nomic-embed-text        # ~270MB, memory search (optional)
```

---

## 2. Install

```bash
cd ~/Desktop/synapse
uv venv --python 3.12
source .venv/bin/activate           # needed in EVERY new terminal
uv pip install -e ".[dev]"
```

macOS has no `python`, only `python3`. After `source`, plain `python` works. If you'd rather not activate,
prefix commands with `uv run` instead.

Optional extras:

```bash
uv pip install -e ".[crew]"         # CrewAI research crew (large install)
uv pip install -e ".[google]"       # Gmail + Calendar
```

---

## 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
SYNAPSE_VAULT_PATH=/Users/you/Documents/YourVault   # the folder Obsidian actually has open
SYNAPSE_MODEL=qwen2.5:7b-instruct

# fast free tier (recommended) — console.groq.com/keys
SYNAPSE_FAST_BASE_URL=https://api.groq.com/openai/v1
SYNAPSE_FAST_MODEL=llama-3.3-70b-versatile
SYNAPSE_FAST_API_KEY=gsk_...
```

The vault path matters: if it isn't the folder Obsidian has open, you'll write notes you never see. The
dashboard header shows a live note count — if it says `0 notes` and your vault isn't empty, the path is wrong.

---

## 4. Check and test

```bash
python -m synapse.cli doctor        # ollama, model, MCP servers, vault path
pytest -q                           # 43 tests
python -m evals.run                 # 16 scenarios
```

Optionally, check the dashboard in a real browser (start the API first, in another terminal):

```bash
pip install playwright && playwright install chromium
python scripts/browser_check.py     # 17 checks: console, a11y, keyboard, responsive, main thread
```

`doctor` failing on Ollama means `ollama serve` isn't running in the other terminal.

---

## 5. Build the dashboard (once)

```bash
cd web && npm install && npm run build && cd ..
```

Rebuild only when you change files under `web/src`. For UI development, `npm run dev` serves on :5173 with
hot reload, proxied to the API.

---

## 6. Run

```bash
python -m synapse.api               # http://127.0.0.1:8000
```

Or headless:

```bash
python -m synapse.cli run "Research LangGraph vs CrewAI and save the comparison to Research/"
```

Daily use after the first setup is three commands: `ollama serve`, `source .venv/bin/activate`,
`python -m synapse.api`.

---

## 7. Optional integrations

**Gmail + Calendar** — Google Cloud Console → new project → enable the Gmail API and Google Calendar API →
OAuth consent screen (External, add yourself as a test user) → Credentials → OAuth client ID → **Desktop app**
→ download JSON → save as `data/google_credentials.json` → run `python -m synapse.integrations.google_auth`
and complete the browser flow. The google MCP server then loads automatically. *Implemented, not verified here.*

**Notion** — create an integration at notion.so/my-integrations, set `SYNAPSE_NOTION_TOKEN`, then share each
page or database with it (⋯ → Connections). Notion 404s anything unshared. *Implemented, not verified here.*

**WhatsApp, Slack, SMS via n8n** — build the workflow behind a Webhook trigger in n8n, then register it:

```bash
SYNAPSE_N8N_WEBHOOKS=whatsapp=https://your-n8n/webhook/abc,slack=https://your-n8n/webhook/def
```

Only registered names can be called. `run_workflow` is high risk and always asks approval. With nothing
registered, the planner is told so and says the integration isn't configured instead of guessing.

**WhatsApp auto-reply (inbound)**

1. In n8n: WhatsApp trigger → HTTP Request node → `POST http://localhost:8000/api/messages` with
   `{"source":"whatsapp","sender":"{{$json.from}}","text":"{{$json.body}}"}` and header
   `Authorization: Bearer $SYNAPSE_API_TOKEN`.
2. Register the outbound direction too, so Synapse can reply:
   `SYNAPSE_N8N_WEBHOOKS=whatsapp=https://your-n8n/webhook/send-whatsapp`.
3. Routine messages get an auto-reply (still gated by approval, since `run_workflow` is high risk).
   Anything needing you triggers a notification with a suggested draft and nothing is sent.

**Scheduling**

The API runs a poll loop (`SYNAPSE_SCHEDULER_INTERVAL`, 30s). Ask in chat — "every weekday at 08:30, brief
me on my tasks and the weather" — or `POST /api/schedules {goal, cadence, at}`. Cadences: `once` (ISO
datetime), `daily`, `weekdays`, `weekly`, `hourly`. Slots missed while the app was closed are skipped, not
fired in a burst.

**Docker**

```bash
VAULT_PATH=/Users/you/Documents/YourVault docker compose up --build
```

Ollama stays on the host (containers get no Metal GPU). *Written, not built here.*

---

## 8. Using it

- **⌘K** opens the command palette (jump to any chat, tab or pending approval), **⌘J** starts a new chat, **/** focuses the prompt. Follow-ups in a chat see the previous turns.
- **Run** is the conversation on the left; the right rail holds the vertical pipeline and the live trace.
- **Agents** shows who is working, on what, with which tool.
- **Vault** is your notes as a graph — drag nodes, click to read. Green means Synapse wrote it.
- **Tools** lists all 23 tools grouped by risk, so you can see what needs approval.
- **Evals** shows the last suite run.

High-risk actions (delete, overwrite, email, calendar, n8n) pause the run and wait for your decision.

---

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| `command not found: python` | venv not activated → `source .venv/bin/activate` |
| `doctor` says ollama unreachable | `ollama serve` not running |
| `0 notes`, nothing in Obsidian | `SYNAPSE_VAULT_PATH` isn't the vault Obsidian opened |
| Obsidian graph has no edges | notes need `[[Wikilinks]]`; ask Synapse to link related notes |
| Runs take minutes | local 7B — add a Groq key (step 3). Small talk is fast either way: it skips planning |
| "recipient has not been used before" | include the email address once; after that it's remembered |
| Note already exists | Synapse appends to it rather than failing |
| "note not found" on delete | Fixed — notes resolve by bare name anywhere in the vault; duplicates are listed rather than guessed |
| Dashboard blank after an edit | Rebuild (`cd web && npm run build`) and check the browser console |
| `search found nothing` | DuckDuckGo rate limit; retry in a minute |
| A tool is "unavailable" in the header | that MCP server failed to start; `doctor` prints why |
