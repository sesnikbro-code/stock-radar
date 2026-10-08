"""Developer helper (not needed for daily use).

  python tools/build_app.py                 regenerate docs/workflow.js (+ icons if Pillow is installed)
  python tools/build_app.py --preview DIR   also build a single-file preview (app + embedded data from DIR/data)
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def write_workflow_js() -> None:
    yml = (ROOT / "ci" / "radar.yml").read_text(encoding="utf-8")
    (DOCS / "workflow.js").write_text(
        "/* generated from ci/radar.yml by tools/build_app.py */\nwindow.RADAR_WORKFLOW = "
        + json.dumps(yml, ensure_ascii=False) + ";\n", encoding="utf-8")


def make_icons() -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not installed - icons not regenerated")
        return
    bg, ring, sweep, blip = (13, 107, 131), (213, 234, 240), (75, 183, 211), (68, 196, 134)

    def draw(size: int, safe: float) -> Image.Image:
        s = size * 4
        im = Image.new("RGB", (s, s), bg)
        d = ImageDraw.Draw(im, "RGBA")
        c, r = s / 2, s / 2 * safe
        d.pieslice([c - r, c - r, c + r, c + r], -90, -30, fill=sweep + (110,))
        for k, w in ((1.0, 0.035), (0.62, 0.022), (0.26, 0.022)):
            rr = r * k
            d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=ring + (255 if k == 1 else 150,), width=max(2, int(s * w)))
        ang = math.radians(-30)
        d.line([c, c, c + r * math.cos(ang), c + r * math.sin(ang)], fill=ring + (255,), width=int(s * 0.03))
        bx, by, br = c + r * 0.5 * math.cos(math.radians(-62)), c + r * 0.5 * math.sin(math.radians(-62)), s * 0.055
        d.ellipse([bx - br, by - br, bx + br, by + br], fill=blip + (255,))
        d.ellipse([c - s * 0.03, c - s * 0.03, c + s * 0.03, c + s * 0.03], fill=ring + (255,))
        return im.resize((size, size), Image.LANCZOS)

    out = DOCS / "icons"
    out.mkdir(parents=True, exist_ok=True)
    draw(192, 0.78).save(out / "icon-192.png")
    draw(512, 0.78).save(out / "icon-512.png")
    draw(512, 0.62).save(out / "icon-maskable-512.png")
    draw(180, 0.74).save(out / "apple-touch-icon.png")


def build_preview(site: Path, out: Path) -> None:
    """One self-contained page: styles, scripts and demo data inlined (for sharing a preview)."""
    data = {}
    for p in sorted((site / "data").rglob("*.json")):
        data[p.relative_to(site / "data").as_posix()] = json.loads(p.read_text(encoding="utf-8"))
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    body = html.split("<!--APP-->")[1].split("<!--/APP-->")[0]
    css = (DOCS / "app.css").read_text(encoding="utf-8")
    js = (DOCS / "app.js").read_text(encoding="utf-8")
    wf = (DOCS / "workflow.js").read_text(encoding="utf-8")
    embed = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = f"""<title>רדאר מניות</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Assistant:wght@400;600;700&family=IBM+Plex+Mono:wght@500;600&family=Secular+One&display=swap">
<style>
html{{direction:rtl}}
{css}
</style>
<div dir="rtl" lang="he">{body}</div>
<script>window.__RADAR_EMBED__={embed};</script>
<script>{wf}</script>
<script>{js}</script>
"""
    out.write_text(page, encoding="utf-8")
    print(f"preview: {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="site folder containing data/ (exported with run.py --export)")
    ap.add_argument("--out", default="preview.html")
    a = ap.parse_args()
    write_workflow_js()
    make_icons()
    if a.preview:
        build_preview(Path(a.preview), Path(a.out))
