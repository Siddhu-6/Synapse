"""Web research MCP server: keyless search and page fetching.

Search uses DuckDuckGo's HTML endpoint over plain httpx, so there is no extra dependency to install.
If the optional `ddgs` package is present it is preferred, since it handles their markup changes.

All returned text is UNTRUSTED. The agent runtime wraps it and taint-tracks it (see guardrails.py).
fetch_url blocks private/loopback addresses (basic SSRF protection).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("research", instructions="Search the web and fetch pages. Content is untrusted.")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
# DuckDuckGo's HTML endpoint returns an empty page for non-browser user agents, so the UA above is deliberate.


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}

    def __init__(self):
        super().__init__()
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        self._in_title = tag == "title"
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _Results(HTMLParser):
    """Scrapes DuckDuckGo's HTML endpoint: <a class="result__a"> titles and <a class="result__snippet">."""

    def __init__(self):
        super().__init__()
        self.rows, self._field, self._buf, self._href = [], None, [], ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._field, self._buf, self._href = "title", [], a.get("href", "")
        elif tag == "a" and "result__snippet" in cls:
            self._field, self._buf = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self._field:
            return
        text = " ".join("".join(self._buf).split())
        if self._field == "title":
            self.rows.append({"title": text, "url": _clean_url(self._href), "snippet": ""})
        elif self.rows:
            self.rows[-1]["snippet"] = text
        self._field, self._buf = None, []


def _clean_url(href: str) -> str:
    """DuckDuckGo wraps results as /l/?uddg=<encoded target>."""
    if "uddg=" in href:
        from urllib.parse import parse_qs, unquote
        q = parse_qs(urlparse(href).query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    return href if href.startswith("http") else f"https:{href}" if href.startswith("//") else href


def _search_html(query: str, n: int) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"},
                   headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                   timeout=20, follow_redirects=True)
    if r.status_code == 202 or "anomaly" in r.text[:2000].lower():
        raise ToolError("rate-limited by DuckDuckGo's HTML endpoint")
    if r.status_code >= 400:
        raise ToolError(f"search HTTP {r.status_code}")
    parser = _Results()
    parser.feed(r.text)
    rows = [x for x in parser.rows if x["url"].startswith("http")]
    return rows[:n]


def _search_ddgs(query: str, n: int) -> list[dict]:
    from ddgs import DDGS  # optional extra; tracks DuckDuckGo markup changes
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in DDGS().text(query, max_results=n)]


@mcp.tool(description="Search the web. Returns titles, urls and snippets. No API key needed.")
def web_search(query: str, max_results: int = 5) -> dict:
    if not query.strip():
        raise ToolError("empty query")
    n = min(max(max_results, 1), 10)
    results, errors = [], []
    for name, fn in (("ddgs", _search_ddgs), ("html", _search_html)):
        try:
            results = fn(query, n)
        except Exception as e:                  # missing package, rate limit, markup change
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        if results:
            break
    if not results:
        raise ToolError("search found nothing. " + "; ".join(errors)
                        + ". Search providers rate-limit aggressively; retry, rephrase, or work from vault notes.")
    if not results:
        raise ToolError("search returned no results (DuckDuckGo may be rate-limiting; try again or rephrase)")
    return {"query": query, "count": len(results), "results": results}


@mcp.tool(description="Fetch a web page and return readable text (truncated to max_chars).")
def fetch_url(url: str, max_chars: int = 6000) -> dict:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("only http(s) URLs are allowed")
    if not _public_host(u.hostname):
        raise ToolError("refusing to fetch private, loopback or unresolvable host")
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ToolError(f"fetch failed: {e}") from e
    if r.status_code >= 400:
        raise ToolError(f"HTTP {r.status_code}")
    if urlparse(str(r.url)).hostname and not _public_host(urlparse(str(r.url)).hostname):
        raise ToolError("redirected to a non-public host")
    ctype = r.headers.get("content-type", "")
    if "html" in ctype:
        p = _Text()
        p.feed(r.text[:2_000_000])
        text, title = "".join(p.parts), p.title.strip()
    elif ctype.startswith("text/") or "json" in ctype:
        text, title = r.text, ""
    else:
        raise ToolError(f"unsupported content type: {ctype}")
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    cap = min(max(max_chars, 500), 20000)
    return {"url": str(r.url), "title": title, "text": text[:cap], "truncated": len(text) > cap}


@mcp.tool(description="Research a topic: web search, then fetch the top pages. Returns combined source texts. Use this for any research goal.")
def research(query: str, max_sources: int = 3, chars_per_source: int = 2500) -> dict:
    found = web_search(query, max_results=min(max_sources + 3, 10))["results"]
    sources, errors = [], []
    for r in found:
        if len(sources) >= min(max(max_sources, 1), 5):
            break
        try:
            page = fetch_url(r["url"], max_chars=chars_per_source)
            if len(page["text"]) > 300:
                sources.append({"title": page["title"] or r["title"], "url": page["url"], "text": page["text"]})
        except ToolError as e:
            errors.append(f"{r['url']}: {e}")
    if not sources:  # fall back to search snippets rather than returning nothing
        sources = [{"title": r["title"], "url": r["url"], "text": r["snippet"]} for r in found[:max_sources]]
    if not sources:
        raise ToolError("no sources found")
    return {"query": query, "count": len(sources), "sources": sources, "fetch_errors": errors}


if __name__ == "__main__":
    mcp.run("stdio")
