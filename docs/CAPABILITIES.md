# What Synapse can do

28 tools across six MCP servers. Risk in brackets: **auto** runs without asking, **ask** pauses for your
approval. Anything marked *needs setup* is in [SETUP.md](../SETUP.md).

## Just talk to it

Greetings, explanations, opinions, "what's the difference between X and Y" — answered in one call, no plan,
no tools, about a second on a hosted model. Anything about the current state of the world (who holds a
position now, current prices, latest versions) is routed to live research instead, because the model's
training data is stale.

## Research and knowledge

| Ask for | What happens |
|---|---|
| "Research X and save it to Research/" | search → fetch pages → write → save → verify (auto + ask to write) |
| "Compare LangGraph, CrewAI and AutoGen" | research, then the CrewAI crew (analyst → writer → critic) |
| "What does the web say about X right now?" | live search and fetch, answered with source URLs |
| "Summarise this page: <url>" | fetch, strip, summarise |
| "Find papers on X and file them" | research + a note per topic, wikilinked |

## Your Obsidian vault

| Ask for | Tool |
|---|---|
| "What do my notes say about X?" | `search_notes`, `gather_notes` [auto] |
| "Save / append this" | `create_note`, `append_to_note` [auto; existing notes get appended, never clobbered] |
| "Rewrite the Setup section of X" | `replace_section` [ask] |
| "Rename / refile this note" | `move_note` [ask] |
| "Tag these notes" | `add_tags` [auto] |
| "What's related to X?" / "find orphan notes" | `related_notes`, `vault_stats`, `list_folders` [auto] |
| "Delete X" | `delete_note` [ask] — by bare name; the note is moved to `.trash/`, not destroyed |

Notes are written with `[[Wikilinks]]` to existing titles, so the Obsidian graph fills in as you go.

## Tasks, planning, scheduling

- "Add a task", "what's on my list", "mark X done" — `add_task`, `list_tasks`, `complete_task`
- "Break this goal into tasks and give me a table with priorities and dates"
- "Every weekday at 08:30, brief me on my open tasks and the weather" — `schedule_task` [ask], runs itself
- "What's scheduled?" / "cancel that" — `list_schedules`, `cancel_schedule`

## Email and calendar *(needs setup)*

- "Email this summary to <address>" — `gmail_send` [ask]; no attachments: the note or draft is written into the
  body itself before you approve, so what you sign is exactly what is sent. Sent as plain text plus clean HTML
  (tables and lists render properly). Bodies with placeholder text ("[Insert …]") are refused and the run replans
  to fetch the real data; a label glued to a Gmail address ("mail-you@gmail.com") is stripped
- "Draft a reply to X" — `gmail_create_draft` [ask]
- "What's in my inbox about X?" — `gmail_search` [auto, treated as untrusted]
- "What's on my calendar tomorrow?" / "block 4pm today" — `calendar_list_events` [auto], `calendar_create_event` [ask]

Addresses you've used before are remembered; an address that only ever appeared in a fetched web page is
blocked outright.

## Notion *(needs setup)*

`notion_search`, `notion_read_page` [auto]; `notion_create_page`, `notion_append`, `notion_add_row` [auto
unless the input is untrusted]. Good for "put this checklist in my Notion tasks database".

## Automation and messaging *(needs setup)*

- "Send X on WhatsApp / Slack / SMS" — `run_workflow` [ask], through an n8n webhook allowlist
- **Inbound auto-reply**: point your n8n WhatsApp trigger at `POST /api/messages`. Synapse classifies the
  message, replies itself if it's routine, and if it needs you it does *not* reply — it notifies you with a
  suggested draft. The incoming text is quoted as data, never executed as instructions.
- "Remind me when it's done" — `notify` [auto], desktop notification

## World

`now` (date/time in any timezone) and `weather` (keyless, any city) — both [auto], mostly used inside daily briefs.

## Multi-step things it does well

- "Research X, save a note, email me the summary and put a review slot on my calendar" — one plan, approvals
  where they matter, verified writes
- "Organise my AI notes": `vault_stats` → find orphans → `related_notes` → append links → verify
- "Build a study roadmap and turn it into tasks": research → table → save → `add_task` per milestone
- "Every morning at 8: weather, open tasks, calendar, and a note in Daily/"

## What it deliberately won't do

Send to an address you never gave it, overwrite a note without the current hash, act on instructions found
inside a web page or message, or loop forever — budgets cap steps, replans and LLM calls. Every write is
read back and hash-checked before it's called done.
