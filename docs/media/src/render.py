"""Render the README pictures in docs/media from the HTML sources in this folder.

    python docs\\media\\src\\render.py

Uses Microsoft Edge's headless mode (no install) and Pillow for the animation (the
python.org Python on this PC has it; the project itself doesn't need it). Writes
banner.png and social-preview.png, how-it-works.png and timestamps.gif. The fonts load
from Google Fonts, so it needs a network connection. scripts/readme_media.py makes the
tour of the running site.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent
MEDIA = SRC.parent
EDGE = next(p for p in (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")) if p.exists())


def shoot(page: str, out: Path, width: int, height: int, scale: int = 2) -> None:
    """One screenshot of a local page at `scale` x its CSS size."""
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--force-device-scale-factor={scale}", f"--window-size={width},{height}",
                        "--virtual-time-budget=8000", f"--user-data-dir={profile}", f"--screenshot={out}",
                        (SRC / page.split("?")[0]).as_uri() + (("?" + page.split("?", 1)[1]) if "?" in page else "")],
                       check=False, capture_output=True, timeout=120)
        for _ in range(60):  # Edge's launcher can return before the file is written
            if out.exists() and out.stat().st_size:
                return
            time.sleep(0.5)
    raise SystemExit(f"no picture for {page}")


def main() -> int:
    shoot("banner.html", MEDIA / "banner.png", 1280, 320)
    shoot("banner.html?social", MEDIA / "social-preview.png", 1280, 640, scale=1)
    shoot("how-it-works.html", MEDIA / "how-it-works.png", 1280, 560)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        frames = []
        for step in range(5):
            png = Path(work) / f"step{step}.png"
            shoot(f"timestamps.html?step={step}", png, 1280, 560, scale=1)
            frames.append(Image.open(png).convert("RGB"))
        palette = frames[-1].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
        # Each step stays up long enough to read; the whole picture longest.
        gif[0].save(MEDIA / "timestamps.gif", save_all=True, append_images=gif[1:],
                    duration=[1600, 2200, 2800, 2800, 5000], loop=0, optimize=True, disposal=1)
    for name in ("banner.png", "social-preview.png", "how-it-works.png", "timestamps.gif"):
        print(f"{name}: {(MEDIA / name).stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
