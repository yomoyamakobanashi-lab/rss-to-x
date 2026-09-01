#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import re
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "funny_clip_transcript_snapshot.json"
USER_AGENT = "Mozilla/5.0 (compatible; ReelPalFunnyClipQA/1.1)"

BANK_PATHS = [
    DATA / "funny_clip_posts.json",
    DATA / "funny_clip_posts_archive.json",
    DATA / "funny_clip_posts_archive_2.json",
    DATA / "funny_clip_posts_archive_3.json",
    DATA / "funny_clip_posts_all_episodes.json",
]


def normalize(value: str) -> str:
    value = html.unescape(str(value or "")).lower()
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’]", "", value)
    return value


def fetch(url: str, timeout: int = 8) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def visible_text(page_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", page_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<svg\b[^>]*>.*?</svg>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|h[1-6]|section|article|main|button)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_unique_episodes() -> list[dict]:
    episodes: OrderedDict[str, dict] = OrderedDict()
    for path in BANK_PATHS:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for item in data:
            title = str(item.get("episode_title") or "").strip()
            url = str(item.get("source_url") or "").strip()
            if not title or not url.startswith("https://listen.style/p/reelpal/"):
                continue
            key = normalize(title)
            if key not in episodes:
                episodes[key] = {"episode_title": title, "source_url": url, "ids": []}
            episodes[key]["ids"].append(str(item.get("id") or ""))
    return list(episodes.values())


def snapshot_episode(episode: dict) -> tuple[bool, dict]:
    try:
        text = visible_text(fetch(episode["source_url"]))
        return True, {**episode, "text": text}
    except Exception as exc:
        return False, {**episode, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    episodes = load_unique_episodes()
    snapshots: list[dict] = []
    failures: list[dict] = []

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(snapshot_episode, episode): episode for episode in episodes}
        done = 0
        for future in as_completed(futures):
            ok, result = future.result()
            done += 1
            if ok:
                snapshots.append(result)
                print(f"[OK] {done}/{len(episodes)} chars={len(result['text'])} {result['episode_title'][:60]}")
            else:
                failures.append(result)
                print(f"[FAIL] {done}/{len(episodes)} {result['source_url']} {result['error']}")

    snapshots.sort(key=lambda x: x["episode_title"])
    failures.sort(key=lambda x: x["episode_title"])
    payload = {
        "summary": {
            "unique_episodes": len(episodes),
            "snapshots": len(snapshots),
            "failures": len(failures),
        },
        "episodes": snapshots,
        "failures": failures,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
