#!/usr/bin/env python
"""Render the deployment guide to PDF using Chromium (already a dev dependency via Playwright).

    python scripts/make_deploy_pdf.py [output.pdf]
"""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "deploy_doc.html"


def chromium() -> str | None:
    explicit = os.environ.get("SYNAPSE_CHROMIUM")
    if explicit and Path(explicit).exists():
        return explicit
    for root in (Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")),
                 Path.home() / ".cache" / "ms-playwright"):
        if root.is_dir():
            for pattern in ("chromium-*/chrome-linux/chrome",
                            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
                hit = next(iter(sorted(root.glob(pattern))), None)
                if hit:
                    return str(hit)
    return None


def main() -> int:
    from playwright.sync_api import sync_playwright

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Synapse-Deployment-Guide.pdf"
    html = SRC.read_text().replace("__DATE__", datetime.date.today().strftime("%d %B %Y"))
    staged = ROOT / ".deploy_doc.rendered.html"
    staged.write_text(html)
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=chromium(), args=["--no-sandbox"])
            pg = b.new_page()
            pg.goto(staged.as_uri(), wait_until="load")
            pg.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
                   header_template="<div></div>",
                   footer_template='<div style="width:100%;font-size:7pt;color:#8b949e;'
                                   'font-family:Helvetica,Arial,sans-serif;padding:0 16mm;">'
                                   '<span style="float:left">Synapse — Setup, Integrations &amp; Deployment Guide</span>'
                                   '<span style="float:right" class="pageNumber"></span></div>',
                   margin={"top": "14mm", "bottom": "16mm", "left": "0", "right": "0"})
            b.close()
    finally:
        staged.unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
