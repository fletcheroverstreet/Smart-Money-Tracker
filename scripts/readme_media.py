"""Make the README's pictures: an animated tour of the site and a strip of phone screens.

Run it while the stack is up (scripts\\dev.ps1), with a Python that has Pillow (the
python.org Python on this PC has it; the project itself doesn't need it):

    python scripts\\readme_media.py

It takes fresh screenshots with Microsoft Edge's headless mode (scripts/screenshots.py)
and writes docs/media/tour.gif and docs/media/phone.png. Each frame of the tour gets a
caption bar saying what the page shows.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshots as shots  # noqa: E402  (the screenshot helpers next to this file)

MEDIA = Path(__file__).resolve().parents[1] / "docs" / "media"
W, H = 1280, 800        # the browser window for the tour
OUT_W = 1100            # the GIF's width (GitHub shows README images up to about 880 px)
CAPTION_H = 64
FRAME_MS = 3200
NAVY, INK, PAPER = (21, 32, 56), (17, 26, 46), (241, 237, 226)
#: (path, caption). Real pages with real filings; trade 338807 is one of Tim Cook's sales.
TOUR = [
    ("/", "Live: insider filings land about a minute after the SEC publishes them"),
    ("/people/timothy-d-cook-1214156?trade=338807", "Every trade opens a quick look with its filing, deadline and footnotes"),
    ("/tickers/DELL", "Ticker pages: an event timeline of buys, sales and planned sales"),
    ("/people/timothy-d-cook-1214156", "People: roles, a summary written only from filings, every trade"),
    ("/people/metropolitan-life-insurance-co-728618", "Form 144 notices of planned sales, linked to the company"),
    ("/tickers", "Tickers ranked by insider buying and selling, with what was left out and why"),
    ("/status", "Status: how fresh the data is, how fast filings arrive, what the checks found"),
]
PHONE = ["/", "/people/timothy-d-cook-1214156", "/status"]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def frame(png: Path, number: int, caption: str) -> Image.Image:
    """The screenshot scaled to OUT_W with a navy caption bar under it."""
    page = Image.open(png).convert("RGB")
    page = page.resize((OUT_W, round(page.height * OUT_W / page.width)), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (OUT_W, page.height + CAPTION_H), NAVY)
    out.paste(page, (0, 0))
    draw = ImageDraw.Draw(out)
    badge = f"{number}/{len(TOUR)}"
    draw.text((24, page.height + CAPTION_H // 2), badge, font=font(18, bold=True), fill=(148, 161, 187), anchor="lm")
    draw.text((84, page.height + CAPTION_H // 2), caption, font=font(22), fill=PAPER, anchor="lm")
    return out


def main() -> int:
    MEDIA.mkdir(parents=True, exist_ok=True)
    browser = shots.edge()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        root = Path(work)
        frames = []
        for i, (path, caption) in enumerate(TOUR, 1):
            png = root / f"tour-{i}.png"
            if not shots.shoot(browser, root / "profile", shots.BASE + path, W, H, png, streams=path == "/"):
                print(f"FAIL {path}")
                return 1
            frames.append(frame(png, i, caption))
            print(f"ok   tour frame {i}: {path}")
        # One palette for every frame keeps the colours steady from frame to frame.
        palette = frames[0].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
        gif[0].save(MEDIA / "tour.gif", save_all=True, append_images=gif[1:], duration=FRAME_MS, loop=0,
                    optimize=True, disposal=1)

        wrapper = root / "frame.html"
        wrapper.write_text(shots.WRAPPER, encoding="utf-8")
        phones = []
        for i, path in enumerate(PHONE, 1):
            png = root / f"phone-{i}.png"
            url = f"{wrapper.as_uri()}?u={quote(shots.BASE + path, safe='')}&w=390&h=844"
            if not shots.shoot(browser, root / "profile", url, 390, 844, png):
                print(f"FAIL phone {path}")
                return 1
            phones.append(Image.open(png).convert("RGB").crop((0, 0, 390, 844)))
            print(f"ok   phone {i}: {path}")
        gap, pad = 28, 32
        strip = Image.new("RGB", (pad * 2 + 390 * 3 + gap * 2, pad * 2 + 844), (233, 238, 245))
        for i, img in enumerate(phones):
            strip.paste(img, (pad + i * (390 + gap), pad))
        strip.save(MEDIA / "phone.png", optimize=True)
    size = (MEDIA / "tour.gif").stat().st_size / 1_048_576
    print(f"docs/media/tour.gif ({size:.1f} MB), docs/media/phone.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
