#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
META_PATH = ROOT / "audiogram_meta.json"
OUT_PATH = ROOT / "audiogram_card.png"
W, H = 1200, 676


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def wrap(text: str, width: int) -> list[str]:
    # textwrap is imperfect for Japanese but still gives deterministic hard caps.
    out = []
    for paragraph in str(text or "").splitlines() or [""]:
        out.extend(textwrap.wrap(paragraph, width=width, break_long_words=True) or [""])
    return out


def main() -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    image = Image.new("RGB", (W, H), (18, 18, 18))
    draw = ImageDraw.Draw(image)

    draw.text((70, 55), "REEL FRIENDS IN TOKYO", font=font(32, True), fill=(245, 245, 245))
    draw.text((70, 105), "リルパル / PODCAST CLIP", font=font(24, False), fill=(205, 205, 205))

    topic = str(meta.get("topic") or "Podcast clip")
    y = 180
    for line in wrap(topic, 20)[:2]:
        draw.text((70, y), line, font=font(48, True), fill=(255, 255, 255))
        y += 62

    caption = str(meta.get("caption") or "")
    y += 18
    for line in wrap(caption, 34)[:4]:
        draw.text((70, y), line, font=font(30, False), fill=(225, 225, 225))
        y += 44

    draw.text((70, 626), "listen.style/p/reelpal", font=font(22, False), fill=(175, 175, 175))
    image.save(OUT_PATH, "PNG")
    print(f"[OK] rendered {OUT_PATH.name} {W}x{H}")


if __name__ == "__main__":
    main()
