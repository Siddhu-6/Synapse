# Guardrails

**Scope:** these reduce risk. They are not a security boundary, and the system should not be pointed at untrusted input without supervision. What follows is what is actually implemented and what it does not cover.

## Layers

1. **Plan-then-execute.** The plan is built from the user's goal before external content is read, so injected text cannot introduce a tool call. A replan sees only step statuses and error strings, never raw retrieved content.
2. **Taint tracking.** Outputs of untrusted tools (`web_search`, `fetch_url`, `research`, `gmail_search`) mark their step as tainted, and anything derived from them inherits it.
3. **Risk policy + approvals.** Client-side table; unknown tools are `high`. `create_note` with `overwrite=true` is escalated from medium to high. Approval is required for any high-risk action, and for medium-risk actions whose input is tainted.
4. **Hard blocks.** Email or calendar recipients that don't appear in the user's own goal are blocked outright, not merely sent for approval. Destructive note operations derived from untrusted content are blocked.
5. **Untrusted content handling.** External text is wrapped in `<untrusted_content source=...>` delimiters, with existing delimiters stripped so they can't be spoofed, and scanned against injection patterns; matches are recorded on the span and surfaced in the dashboard. The system prompt instructs the model to treat delimited text as data.
6. **Budgets.** Max steps per plan, max replans, max LLM calls per run, per-tool timeouts, note size cap, recursion limit.
7. **Validation.** Structured model output is parsed into Pydantic models with one repair round; plans are semantically validated; tool arguments are checked against the tool's JSON Schema before the call.
8. **Vault safety.** Paths are resolved and confined to the vault, hidden folders such as `.obsidian` are refused, writes are atomic via a temp file and rename, `create_note` never overwrites silently, and `update_note` requires the current hash.
9. **Redaction.** API keys and similar patterns are redacted before traces are written to disk.

## What these do not cover

- **A determined prompt injection may still influence generated text.** Pattern matching catches the obvious phrasings, not paraphrases. The real protection is structural: injected text cannot call a tool, and consequential tools require your approval.
- **No sandboxing of MCP servers.** They are local processes with the permissions of the user who started them.
- **No output filtering** for harmful or false content beyond verification of writes.
- **No multi-user authorisation.** A single bearer token is the whole model; it is not per-user, has no roles and no rate limiting.
- **No egress restrictions.** `fetch_url` refuses private and loopback hosts to limit SSRF, but a public URL can be fetched.
- **Local models are weak at instruction-following.** A 1.5B model hallucinated a file hash during testing. The concurrency check caught it, which is the point: guardrails assume the model will be wrong.
