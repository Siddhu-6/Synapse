"""Obsidian vault MCP server. Operates on the markdown files directly; Obsidian need not be running.

Write tools return a `verify` recipe (tool + args + expected hash) so the agent can prove the write landed.
Deletes move notes to `.trash/` (Obsidian's own convention), so they are recoverable.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

VAULT = Path(os.environ.get("SYNAPSE_VAULT_PATH", "./SynapseVault")).expanduser().resolve()
VAULT.mkdir(parents=True, exist_ok=True)
MAX_NOTE_BYTES = 512_000

mcp = FastMCP("vault", instructions="Read, create, update and search markdown notes in the user's Obsidian vault.")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _resolve(rel: str, must_exist: bool = False) -> Path:
    """Vault-relative path -> absolute path, confined to the vault.

    With must_exist, a path that does not resolve is looked up BY NAME anywhere in the vault.
    Users say "delete Lionel Messi", not "delete Research/Lionel Messi.md", and the planner has no
    reliable way to know the folder — so a bare name has to find the note or the tool is unusable.
    An ambiguous name returns the candidates instead of guessing.
    """
    rel = rel.strip().replace("\\", "/").lstrip("/")
    if not rel or "\x00" in rel:
        raise ToolError("empty or invalid path")
    if not rel.endswith(".md"):
        rel += ".md"
    p = (VAULT / rel).resolve()
    if not p.is_relative_to(VAULT):
        raise ToolError(f"path escapes the vault: {rel}")
    if any(part.startswith(".") for part in p.relative_to(VAULT).parts):
        raise ToolError("hidden paths (e.g. .obsidian, .trash) are not accessible via this tool")
    if must_exist and not p.is_file():
        matches = sorted(f for f in VAULT.rglob(p.name)
                         if f.is_file() and not any(x.startswith(".") for x in f.relative_to(VAULT).parts))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolError(f"'{p.stem}' matches several notes: "
                            + ", ".join(m.relative_to(VAULT).as_posix() for m in matches[:8])
                            + ". Pass the full path.")
    return p


def _rel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


def _frontmatter(tags: list[str]) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    clean = [re.sub(r"[^\w/-]", "", t) for t in tags]
    return f"---\ncreated: {now}\ncreated_by: synapse\ntags: [{', '.join(t for t in clean if t)}]\n---\n\n"


def _write(p: Path, body: str) -> dict:
    if len(body.encode()) > MAX_NOTE_BYTES:
        raise ToolError("note too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)  # atomic on the same filesystem
    h = _sha(body)
    return {"path": _rel(p), "bytes": len(body.encode()), "sha256": h,
            "verify": {"tool": "read_note", "args": {"path": _rel(p)}, "expect_sha256": h}}


def _notes():
    for p in sorted(VAULT.rglob("*.md")):
        if not any(part.startswith(".") for part in p.relative_to(VAULT).parts):
            yield p


@mcp.tool(description="Create a markdown note. Fails if it exists unless overwrite=true. Use [[Note Name]] to link notes.")
def create_note(path: str, content: str, tags: list[str] | None = None, overwrite: bool = False) -> dict:
    p = _resolve(path)
    if p.exists() and not overwrite:
        raise ToolError(f"note already exists: {_rel(p)} (use append_to_note, a new path, or overwrite=true)")
    body = content if content.lstrip().startswith("---") else _frontmatter(tags or []) + content
    return _write(p, body)


@mcp.tool(description="Append markdown to the end of an existing note (optionally under a new '## heading').")
def append_to_note(path: str, content: str, heading: str | None = None) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    old = p.read_text(encoding="utf-8")
    block = (f"\n\n## {heading.strip()}\n\n" if heading else "\n\n") + content.strip() + "\n"
    return _write(p, old.rstrip() + block)


@mcp.tool(description="Replace a note's full content. expected_sha256 (from read_note) prevents overwriting concurrent edits.")
def update_note(path: str, content: str, expected_sha256: str) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    if _sha(p.read_text(encoding="utf-8")) != expected_sha256:
        raise ToolError("note changed since it was read (sha mismatch); read it again first")
    return _write(p, content)


@mcp.tool(description="Read a note. Returns content and sha256.")
def read_note(path: str) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    text = p.read_text(encoding="utf-8")
    return {"path": _rel(p), "content": text, "sha256": _sha(text)}


@mcp.tool(description="Case-insensitive search over note names and content. Returns paths with snippets.")
def search_notes(query: str, limit: int = 10) -> dict:
    q = query.lower().strip()
    if not q:
        raise ToolError("empty query")
    terms, hits = q.split(), []
    for p in _notes():
        text = p.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        score = sum(low.count(t) for t in terms) + 5 * sum(t in p.stem.lower() for t in terms)
        if score:
            i = max(low.find(terms[0]), 0)
            hits.append({"path": _rel(p), "score": score, "snippet": text[max(i - 60, 0):i + 160].replace("\n", " ")})
    hits.sort(key=lambda h: -h["score"])
    return {"query": query, "count": len(hits), "results": hits[:min(max(limit, 1), 50)]}


@mcp.tool(description="Search the vault and return the FULL content of the best matching notes (for summarising/studying existing notes).")
def gather_notes(query: str, max_notes: int = 5, chars_per_note: int = 4000) -> dict:
    hits = search_notes(query, limit=max_notes)["results"]
    notes = []
    for h in hits:
        text = (VAULT / h["path"]).read_text(encoding="utf-8")
        notes.append({"path": h["path"], "content": text[:chars_per_note], "truncated": len(text) > chars_per_note})
    return {"query": query, "count": len(notes), "notes": notes}


@mcp.tool(description="List notes, optionally within a folder.")
def list_notes(folder: str = "") -> dict:
    base = (VAULT / folder.strip("/")).resolve()
    if not base.is_relative_to(VAULT):
        raise ToolError("folder escapes the vault")
    notes = [_rel(p) for p in _notes() if p.is_relative_to(base)]
    return {"folder": folder, "count": len(notes), "notes": notes[:500]}


@mcp.tool(description="Delete a note (moved to .trash/, recoverable). Destructive: always requires user approval.")
def delete_note(path: str) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    dest = VAULT / ".trash" / f"{datetime.now():%Y%m%d%H%M%S}_{p.name}"
    dest.parent.mkdir(exist_ok=True)
    p.replace(dest)
    return {"deleted": _rel(p), "trash_path": dest.relative_to(VAULT).as_posix()}


@mcp.tool(description="List folders in the vault with note counts. Use before organising notes.")
def list_folders() -> dict:
    counts: dict[str, int] = {}
    for f in VAULT.rglob("*.md"):
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        counts[(f.parent.relative_to(VAULT).as_posix() or "/")] = counts.get(
            f.parent.relative_to(VAULT).as_posix() or "/", 0) + 1
    return {"folders": [{"folder": k, "notes": v} for k, v in sorted(counts.items())], "total_notes": sum(counts.values())}


@mcp.tool(description="Move or rename a note, keeping its content. Fails if the destination exists.")
def move_note(path: str, new_path: str) -> dict:
    src, dst = _resolve(path, must_exist=True), _resolve(new_path)
    if not src.is_file():
        raise ToolError(f"note not found: {_rel(src)}")
    if dst.exists():
        raise ToolError(f"destination already exists: {_rel(dst)}")
    body = src.read_text(encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"from": _rel(src), "to": _rel(dst), "sha256": _sha(body),
            "verify": {"tool": "read_note", "args": {"path": _rel(dst)}, "expect_sha256": _sha(body)}}


@mcp.tool(description="Replace one '## heading' section of a note, leaving the rest untouched.")
def replace_section(path: str, heading: str, content: str) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    text = p.read_text(encoding="utf-8")
    lines, out, level, replaced, skipping = text.splitlines(), [], None, False, False
    want = heading.strip().lstrip("#").strip().lower()
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if m.group(2).strip().lower() == want and not replaced:
                out += [line, "", content.strip(), ""]
                level, replaced, skipping = len(m.group(1)), True, True
                continue
            if skipping and len(m.group(1)) <= level:
                skipping = False
        if not skipping:
            out.append(line)
    if not replaced:
        raise ToolError(f"heading '{heading}' not found in {_rel(p)}. Headings: "
                        + ", ".join(re.findall(r"^#{1,6}\s+(.*)$", text, re.M)[:10]))
    body = "\n".join(out).rstrip() + "\n"
    p.write_text(body, encoding="utf-8")
    return {"path": _rel(p), "heading": heading, "bytes": len(body.encode()), "sha256": _sha(body),
            "verify": {"tool": "read_note", "args": {"path": _rel(p)}, "expect_sha256": _sha(body)}}


@mcp.tool(description="Add tags to a note's frontmatter without touching its body.")
def add_tags(path: str, tags: list[str]) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    text = p.read_text(encoding="utf-8")
    clean = [re.sub(r"[^\w/-]", "", x) for x in tags if x.strip()]
    if not clean:
        raise ToolError("no valid tags given")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m:
        fm = m.group(1)
        existing = re.search(r"^tags:\s*\[(.*?)\]", fm, re.M)
        current = [x.strip() for x in (existing.group(1) if existing else "").split(",") if x.strip()]
        merged = sorted(set(current) | set(clean))
        fm_new = (re.sub(r"^tags:.*$", f"tags: [{', '.join(merged)}]", fm, flags=re.M) if existing
                  else fm + f"\ntags: [{', '.join(merged)}]")
        body = f"---\n{fm_new}\n---\n" + text[m.end():]
    else:
        merged = sorted(set(clean))
        body = f"---\ntags: [{', '.join(merged)}]\n---\n\n" + text
    p.write_text(body, encoding="utf-8")
    return {"path": _rel(p), "tags": merged, "sha256": _sha(body),
            "verify": {"tool": "read_note", "args": {"path": _rel(p)}, "expect_sha256": _sha(body)}}


@mcp.tool(description="Find notes related to one note, by shared wikilinks, tags and title words.")
def related_notes(path: str, limit: int = 8) -> dict:
    p = _resolve(path, must_exist=True)
    if not p.is_file():
        raise ToolError(f"note not found: {_rel(p)}")
    text = p.read_text(encoding="utf-8")
    links = {x.lower() for x in re.findall(r"\[\[([^\]|#]+)", text)}
    tags = {x.lower() for x in re.findall(r"^tags:\s*\[(.*?)\]", text, re.M) for x in x.split(",")}
    words = {w for w in re.findall(r"[a-zA-Z]{5,}", p.stem.lower())}
    scored = []
    for other in VAULT.rglob("*.md"):
        if other == p or any(x.startswith(".") for x in other.relative_to(VAULT).parts):
            continue
        o = other.read_text(encoding="utf-8", errors="ignore")
        o_links = {x.lower() for x in re.findall(r"\[\[([^\]|#]+)", o)}
        o_tags = {x.strip().lower() for m in re.findall(r"^tags:\s*\[(.*?)\]", o, re.M) for x in m.split(",")}
        score = (2 * len(links & o_links) + 2 * len({t.strip() for t in tags} & o_tags)
                 + len(words & {w for w in re.findall(r"[a-zA-Z]{5,}", other.stem.lower())})
                 + (3 if p.stem.lower() in o.lower() else 0))
        if score:
            scored.append({"path": _rel(other), "score": score})
    scored.sort(key=lambda x: -x["score"])
    return {"path": _rel(p), "related": scored[:min(max(limit, 1), 25)]}


@mcp.tool(description="Vault overview: note count, folders, orphan notes with no links, most linked notes.")
def vault_stats() -> dict:
    notes, links = {}, {}
    for f in VAULT.rglob("*.md"):
        if any(x.startswith(".") for x in f.relative_to(VAULT).parts):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        notes[_rel(f)] = {"stem": f.stem.lower(), "out": re.findall(r"\[\[([^\]|#]+)", text), "bytes": len(text)}
    stems = {v["stem"]: k for k, v in notes.items()}
    for path, n in notes.items():
        for target in n["out"]:
            dst = stems.get(target.strip().lower())
            if dst and dst != path:
                links[dst] = links.get(dst, 0) + 1
                links[path] = links.get(path, 0) + 0
    orphans = [p for p in notes if not links.get(p) and not notes[p]["out"]]
    top = sorted(links.items(), key=lambda x: -x[1])[:5]
    return {"notes": len(notes), "total_bytes": sum(n["bytes"] for n in notes.values()),
            "orphans": orphans[:20], "orphan_count": len(orphans),
            "most_linked": [{"path": p, "inbound": c} for p, c in top if c]}


if __name__ == "__main__":
    mcp.run("stdio")
