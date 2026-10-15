"""Take the demo screenshots of the running site at 1440 and 390 px (docs/screenshots/).

Uses Microsoft Edge's headless mode, which is already on Windows: nothing to
install. Run it while the stack is up (scripts\\dev.ps1):

    python scripts\\screenshots.py                # every page, both widths
    python scripts\\screenshots.py status tickers # only pages whose name matches

Edge can't make its window narrower than about 500 px, so the phone shots load
the page inside a 390 px wide frame (a small local wrapper page): the page then
really is laid out at 390 px, as on a phone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

#: SMT_BASE points the script at another copy of the site, SMT_SHOTS at another folder.
BASE = os.environ.get("SMT_BASE", "http://localhost:3000").rstrip("/")
OUT = Path(os.environ.get("SMT_SHOTS") or Path(__file__).resolve().parents[1] / "docs" / "screenshots" / "phase1")
EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
#: (file name, path). The person and tickers are real ones with enough filings to fill the page.
PAGES = [
    ("live", "/"),
    ("people", "/people"),
    ("person", "/people/timothy-d-cook-1214156"),
    # A seller who has filed only Form 144 notices: the profile lists them.
    ("seller-notices", "/people/metropolitan-life-insurance-co-728618"),
    ("tickers", "/tickers"),
    ("ticker", "/tickers/DELL"),
    ("ticker-other-currency", "/tickers/UMC"),
    ("status", "/status"),
    ("methodology", "/methodology"),
    ("sources", "/sources"),
    ("watchlist", "/watchlist"),
]
#: (label, width, height). The heights show more than one screen of each page.
SIZES = [("1440", 1440, 1600), ("390", 390, 1400)]
WRAPPER = """<!doctype html><meta charset="utf-8"><title>frame</title>
<body style="margin:0;background:#000"><script>
const q = new URLSearchParams(location.search);
const f = document.createElement("iframe");
f.src = q.get("u");
f.style.cssText = `border:0;display:block;width:${q.get("w")}px;height:${q.get("h")}px`;
document.body.appendChild(f);
</script>"""


def edge() -> Path:
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit("Microsoft Edge wasn't found in Program Files.")


def shoot(browser: Path, profile: Path, url: str, width: int, height: int, target: Path,
          streams: bool = False) -> bool:
    """One screenshot. Edge lets the page run for up to 20 s of page time so the data has arrived.

    `streams`: the page keeps a connection open (Live), so its page time never runs out and
    Edge would wait for ever; such a page is shot when it has loaded instead (its first rows
    come with the page). Each shot gets its own browser profile, or a second Edge would hand
    its page to one still running from the shot before and exit without a picture."""
    target.unlink(missing_ok=True)
    wait = ["--timeout=20000"] if streams else ["--virtual-time-budget=20000"]
    subprocess.run(
        [str(browser), "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={width},{height}", *wait,
         f"--user-data-dir={profile / target.stem}", f"--screenshot={target}", url],
        check=False, capture_output=True, timeout=180)
    # Edge's launcher can return before the browser process has written the file.
    for _ in range(120):
        if target.exists() and target.stat().st_size > 0:
            return True
        time.sleep(0.5)
    return False


def main(only: list[str]) -> int:
    browser = edge()
    OUT.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    # Edge can still hold its profile files for a moment after it exits: leave them to Windows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        wrapper = Path(work) / "frame.html"
        wrapper.write_text(WRAPPER, encoding="utf-8")
        profile = Path(work) / "profile"
        for name, path in PAGES:
            if only and not any(word in name for word in only):
                continue
            for label, width, height in SIZES:
                target = OUT / f"{name}-{label}.png"
                page = BASE + path
                # Narrow shots go through the wrapper (see the module docstring).
                url = page if width >= 500 else f"{wrapper.as_uri()}?u={quote(page, safe='')}&w={width}&h={height}"
                # Framed (narrow) shots settle normally: the wrapper page itself has no stream.
                ok = shoot(browser, profile, url, width, height, target, streams=path == "/" and width >= 500)
                print(f"{'ok  ' if ok else 'FAIL'} {target.name}")
                if not ok:
                    failed.append(target.name)
    if failed:
        print(f"{len(failed)} screenshot(s) failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
