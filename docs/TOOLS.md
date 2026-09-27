# Tools

All tools are exposed through MCP servers (separate processes, stdio). Risk levels are assigned client-side; `high` always requires approval, `medium` requires it when the input is tainted by untrusted content.

## vault — Obsidian (`synapse/mcp_servers/vault.py`)

Operates directly on markdown files, so Obsidian does not need to be running.

| Tool | Risk | Arguments | Notes |
|---|---|---|---|
| `read_note` | low | `path` | returns `content` + `sha256` |
| `search_notes` | low | `query`, `limit` | title + content match with snippets |
| `gather_notes` | low | `query`, `max_notes`, `chars_per_note` | full contents for summarising or study guides |
| `list_notes` | low | `folder` | paths, sizes, modified times |
| `create_note` | medium | `path`, `content`, `tags`, `overwrite` | fails if it exists unless `overwrite`; `overwrite=true` is escalated to high |
| `append_to_note` | medium | `path`, `content`, `heading` | appends under a heading if given |
| `update_note` | high | `path`, `content`, `expected_sha256` | rejected on hash mismatch (optimistic concurrency) |
| `delete_note` | high | `path` | always needs approval |

Writes are atomic (temp file + rename), add frontmatter when missing, and return a `verify` recipe `{tool, args, expect_sha256}` that the graph's verifier replays independently.

Failure modes: path escapes the vault, hidden folders (`.obsidian`), note already exists, note missing, hash mismatch, oversized note. All return a clear message to the planner rather than raising.

## vault — organising

| Tool | Risk | Arguments | Notes |
|---|---|---|---|
| `list_folders` | low | — | folders with note counts |
| `related_notes` | low | `path`, `limit` | scores other notes by shared wikilinks, tags and title words |
| `vault_stats` | low | — | note count, orphans with no links, most-linked notes |
| `add_tags` | medium | `path`, `tags` | merges into frontmatter, leaves the body alone |
| `move_note` | high | `path`, `new_path` | rename or refile; fails if the destination exists |
| `replace_section` | high | `path`, `heading`, `content` | rewrites one `## heading` in place; on a miss it returns the headings that do exist, so the planner can correct itself |

## tasks (`synapse/mcp_servers/tasks.py`)

SQLite-backed todo list. `add_task` (low, idempotent on title+project), `list_tasks` (low), `complete_task` (medium).

## research (`synapse/mcp_servers/research.py`)

`web_search`, `fetch_url` (HTML → text), `research` (search + fetch top sources in one call). Search tries `ddgs` (multiple backends) and falls back to scraping DuckDuckGo's HTML endpoint, so no API key is needed; both are rate-limited, and the error says so plainly when they are. All low risk but **untrusted**: outputs taint their step. `fetch_url` refuses private, loopback and link-local hosts, caps response size and strips scripts and styles.

## google (`synapse/mcp_servers/google.py`) — optional

Loaded only when `data/google_token.json` exists. `gmail_search` (low, untrusted), `gmail_create_draft` (medium), `calendar_list_events` (low), `gmail_send` (high), `calendar_create_event` (high). Recipients not present in the user's goal are blocked before approval is even offered. Implemented but not yet verified live — see [LIMITATIONS](LIMITATIONS.md).

## Adding a tool

1. Write the function in an MCP server, raising `ToolError` with a message the planner can act on.
2. Add its risk level to `RISK_POLICY` in `synapse/guardrails.py` — unlisted tools default to `high`.
3. If it is read-only, add it to `READ_ONLY` so timeouts can be retried.
4. If it returns external content, add it to `UNTRUSTED_TOOLS`.
5. For writes, return a `verify` recipe.
6. Add an eval scenario.
