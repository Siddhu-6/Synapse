#!/usr/bin/env python
"""Browser checks against a running Synapse dashboard, in real Chromium.

These catch a class of bug the unit tests structurally cannot: a runtime crash that still builds
cleanly, an unlabelled control, a layout that overflows on a phone, or a render loop that locks the
main thread. All of those have happened in this project — a missing `const TABS` produced a blank
white page that `vite build` was perfectly happy with, which is why this file exists.

    # terminal 1
    python -m synapse.api

    # terminal 2
    pip install playwright && playwright install chromium
    python scripts/browser_check.py                    # defaults to http://127.0.0.1:8000

Exits non-zero if any check fails. Set SYNAPSE_URL to point elsewhere, SYNAPSE_CHROMIUM to use a
specific Chromium binary.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

URL = os.environ.get("SYNAPSE_URL", "http://127.0.0.1:8000")
OUT = Path(os.environ.get("SYNAPSE_SHOTS", "/tmp/synapse-shots"))
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   — {detail}"), flush=True)
    return bool(ok)


def chromium() -> str | None:
    explicit = os.environ.get("SYNAPSE_CHROMIUM")
    if explicit and Path(explicit).exists():
        return explicit
    roots = [Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")),
             Path.home() / ".cache" / "ms-playwright"]
    for root in roots:
        if root.is_dir():
            for pattern in ("chromium-*/chrome-linux/chrome",
                            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
                hit = next(iter(sorted(root.glob(pattern))), None)
                if hit:
                    return str(hit)
    return None                                     # let Playwright use its own default


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright not installed — pip install playwright && playwright install chromium")
    raise SystemExit(2) from None

OUT.mkdir(parents=True, exist_ok=True)
console: list[str] = []
crashes: list[str] = []
print(f"\nbrowser checks against {URL}\n", flush=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=chromium(), args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:140]}"))
    page.on("pageerror", lambda e: crashes.append(str(e)[:180]))

    # SSE holds a request open for the lifetime of the page, so networkidle would never fire.
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    noisy = [m for m in console if m.startswith(("error", "warning"))]
    check("loads without console errors or warnings", not noisy and not crashes,
          "; ".join((crashes + noisy)[:2]))

    # ---- structure and labelling -------------------------------------------------------------
    check("skip link present", page.locator("a:has-text('Skip to content')").count() == 1)
    check("tablist exposes all six views",
          page.get_by_role("tablist").count() == 1 and page.get_by_role("tab").count() == 6,
          f"{page.get_by_role('tab').count()} tabs")
    check("main region is a labelled tabpanel", page.locator("#main[role=tabpanel]").count() == 1)
    check("exactly one tab is current", page.locator("[role=tab][aria-selected=true]").count() == 1)
    check("prompt input is labelled", page.locator("#goal-input").count() == 1)

    # ---- command palette, keyboard only ------------------------------------------------------
    page.keyboard.press("Control+k")
    page.wait_for_timeout(300)
    check("Ctrl/Cmd-K opens the palette",
          page.get_by_role("dialog", name="Command palette").count() == 1)
    check("palette takes focus on open",
          page.evaluate("document.activeElement?.getAttribute('role')") == "combobox")

    page.keyboard.type("vault")
    page.wait_for_timeout(250)
    check("palette filters as you type", page.get_by_role("option").count() >= 1)

    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    check("Enter activates and dismisses", page.get_by_role("dialog").count() == 0)
    # accessible name, not raw text: the decorative "03" index is aria-hidden
    selected = page.get_by_role("tab", name="Vault", exact=True).get_attribute("aria-selected")
    check("activation actually switched view", selected == "true", f"Vault aria-selected={selected!r}")

    page.keyboard.press("Control+k")
    page.wait_for_timeout(250)
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    check("Escape dismisses the palette", page.get_by_role("dialog").count() == 0)

    # ---- responsive ---------------------------------------------------------------------------
    for w, h in ((320, 640), (768, 900), (1024, 800), (1440, 900)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(350)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        check(f"no horizontal overflow at {w}px", not overflow)
        page.screenshot(path=str(OUT / f"{w}.png"))

    # ---- the freeze regression ----------------------------------------------------------------
    # Revealing an answer used to re-parse the whole markdown ~60x/second and replace the DOM each
    # time, which locked the main thread and produced Chrome's "Page Unresponsive" dialog.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    page.evaluate("""() => {
        window.__worst = 0; window.__last = performance.now();
        window.__probe = setInterval(() => {
            const n = performance.now();
            window.__worst = Math.max(window.__worst, n - window.__last - 16);
            window.__last = n;
        }, 16);
    }""")
    page.wait_for_timeout(3000)
    worst = page.evaluate("() => { clearInterval(window.__probe); return Math.round(window.__worst); }")
    check("main thread stays responsive while an answer is revealed", worst < 250, f"blocked {worst}ms")

    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed    screenshots: {OUT}\n", flush=True)
sys.exit(1 if failed else 0)
