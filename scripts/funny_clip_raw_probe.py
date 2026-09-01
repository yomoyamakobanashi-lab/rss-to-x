#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "funny_clip_raw_probe.json"
USER_AGENT = "Mozilla/5.0 (compatible; ReelPalFunnyClipQA/1.0)"

PROBES = [
    {
        "id": "all-episode-009",
        "url": "https://listen.style/p/reelpal/lt7ujaek",
        "needles": ["ピーノさん", "みんな、おしゃべり", "去年の映画"],
    },
    {
        "id": "all-episode-033",
        "url": "https://listen.style/p/reelpal/08x9zj0r",
        "needles": ["ハムナプトラ見ちゃった", "見たくなって見ちゃった", "ハムナプトラ"],
    },
    {
        "id": "all-episode-075",
        "url": "https://listen.style/p/reelpal/xtzntwon",
        "needles": ["百人一首", "ちはやふる", "枕言葉"],
    },
    {
        "id": "all-episode-095",
        "url": "https://listen.style/p/reelpal/ow4mha5o",
        "needles": ["ミッシングチャイルドビデオテープ", "怖いやつ", "プライムビデオ"],
    },
    {
        "id": "all-episode-114",
        "url": "https://listen.style/p/reelpal/gnrdz2nn",
        "needles": ["通信が飛んでました", "マコちゃんが止まっていた", "しゃべり続けていた"],
    },
    {
        "id": "all-episode-117",
        "url": "https://listen.style/p/reelpal/mwnhzsa5",
        "needles": ["今日なんか普通だね", "1回じゃ済まない", "その1"],
    },
]

MEDIA_RE = re.compile(
    r'https?[^"\'<> ]+?\.(?:mp3|m4a|aac|mp4)(?:[^"\'<> ]*)?',
    re.IGNORECASE,
)


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


def media_urls(body: str) -> list[str]:
    urls: list[str] = []
    for match in MEDIA_RE.findall(body):
        url = html.unescape(match).replace("\\/", "/")
        if url not in urls:
            urls.append(url)
    return urls


def main() -> None:
    results: list[dict] = []
    for probe in PROBES:
        body = fetch(probe["url"])
        result = {
            "id": probe["id"],
            "url": probe["url"],
            "html_chars": len(body),
            "media_urls": media_urls(body),
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
        for marker in ["wire:snapshot", "transcript", "Transcript", "文字起こし", "episodeTranscript", "livewire", "__NEXT_DATA__", "audioUrl", "audio_url"]:
            result["markers"][marker] = body.lower().count(marker.lower())
        results.append(result)
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(
            result["id"],
            result["html_chars"],
            "media=", len(result["media_urls"]),
            {k: v["count"] for k, v in result["needles"].items()},
            result["markers"],
        )


if __name__ == "__main__":
    main()
