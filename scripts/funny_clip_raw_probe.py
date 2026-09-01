#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "funny_clip_raw_probe.json"
USER_AGENT = "Mozilla/5.0 (compatible; ReelPalFunnyClipQA/1.0)"

PROBES = [
    {
        "id": "all-episode-033",
        "url": "https://listen.style/p/reelpal/08x9zj0r",
        "needles": ["ハムナプトラ見ちゃった", "見たくなって見ちゃった", "ハムナプトラ"],
    },
    {
        "id": "all-episode-072",
        "url": "https://listen.style/p/reelpal/q8ghoojk",
        "needles": ["ホビットのイボンヌ", "ビルボです", "イボンヌ"],
    },
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def contexts(body: str, needle: str, radius: int = 500) -> list[str]:
    found: list[str] = []
    start = 0
    while True:
        index = body.find(needle, start)
        if index < 0:
            break
        found.append(body[max(0, index - radius): min(len(body), index + len(needle) + radius)])
        start = index + len(needle)
        if len(found) >= 5:
            break
    return found


def main() -> None:
    results: list[dict] = []
    for probe in PROBES:
        body = fetch(probe["url"])
        result = {
            "id": probe["id"],
            "url": probe["url"],
            "html_chars": len(body),
            "needles": {},
            "markers": {},
            # Full raw HTML is intentionally included only in this temporary QA artifact.
            "raw_html": body,
        }
        for needle in probe["needles"]:
            result["needles"][needle] = {
                "count": body.count(needle),
                "contexts": contexts(body, needle),
            }
        for marker in ["wire:snapshot", "transcript", "Transcript", "文字起こし", "episodeTranscript", "livewire", "__NEXT_DATA__"]:
            result["markers"][marker] = body.lower().count(marker.lower())
        results.append(result)
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(result["id"], result["html_chars"], {k: v["count"] for k, v in result["needles"].items()}, result["markers"])


if __name__ == "__main__":
    main()
