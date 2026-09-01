#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORT_PATH = ROOT / "funny_clip_quality_report.json"

BANK_PATHS = [
    DATA / "funny_clip_posts.json",
    DATA / "funny_clip_posts_archive.json",
    DATA / "funny_clip_posts_archive_2.json",
    DATA / "funny_clip_posts_archive_3.json",
    DATA / "funny_clip_posts_all_episodes.json",
]
ALL_EPISODES_PATH = DATA / "funny_clip_posts_all_episodes.json"
SPOTIFY_EPISODES_PATH = DATA / "spotify_episodes.json"
USER_AGENT = "rss-to-x funny-clip QA/1.1"


def normalize(value: str) -> str:
    value = html.unescape(str(value or "")).lower()
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’]", "", value)
    return value


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_page_title(page_html: str) -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    match = re.search(r"<title>(.*?)</title>", page_html, flags=re.I | re.S)
    if match:
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
        title = re.sub(r"\s+-\s+.*?LISTEN.*$", "", title).strip()
        return title
    return ""


def build_spotify_index() -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for episode in load_json(SPOTIFY_EPISODES_PATH):
        url = str(episode.get("spotifyUrl") or "").strip()
        title = str(episode.get("title") or "").strip()
        if title and url.startswith("https://open.spotify.com/episode/"):
            index[normalize(title)].append({"title": title, "url": url})
        normalized_title = str(episode.get("normalizedTitle") or "").strip()
        if normalized_title and url.startswith("https://open.spotify.com/episode/"):
            key = normalize(normalized_title)
            if not any(x["url"] == url for x in index[key]):
                index[key].append({"title": title or normalized_title, "url": url})
    return dict(index)


def load_bank_items() -> list[dict]:
    items: list[dict] = []
    for path in BANK_PATHS:
        if not path.exists():
            continue
        chunk = load_json(path)
        if not isinstance(chunk, list):
            continue
        for item in chunk:
            copy = dict(item)
            copy["_bank"] = path.name
            items.append(copy)
    return items


def group_by_episode(bank: list[dict]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    for item in bank:
        title = str(item.get("episode_title") or "").strip()
        if not title:
            continue
        key = normalize(title)
        group = grouped.setdefault(key, {
            "episode_title": title,
            "ids": [],
            "stored_urls": set(),
            "banks": set(),
        })
        group["ids"].append(str(item.get("id") or ""))
        source_url = str(item.get("source_url") or "").strip()
        if source_url:
            group["stored_urls"].add(source_url)
        group["banks"].add(str(item.get("_bank") or ""))
    return grouped


def main() -> None:
    bank = load_bank_items()
    grouped = group_by_episode(bank)
    all_episode_items = load_json(ALL_EPISODES_PATH)
    spotify_index = build_spotify_index()

    spotify_mismatches: list[dict] = []
    spotify_missing: list[dict] = []
    for item in all_episode_items:
        title = str(item.get("episode_title") or "")
        matches = spotify_index.get(normalize(title), [])
        if len(matches) != 1:
            spotify_missing.append({
                "id": item.get("id"),
                "episode_title": title,
                "stored": item.get("spotify_url"),
                "matches": matches,
            })
            continue
        canonical = matches[0]["url"]
        stored = str(item.get("spotify_url") or "").strip()
        if stored != canonical:
            spotify_mismatches.append({
                "id": item.get("id"),
                "episode_title": title,
                "stored": stored,
                "canonical": canonical,
            })

    listen_mismatches: list[dict] = []
    listen_fetch_errors: list[dict] = []
    listen_multiple_urls: list[dict] = []
    verified_listen_titles = 0

    for number, group in enumerate(grouped.values(), start=1):
        urls = sorted(group["stored_urls"])
        base = {
            "episode_title": group["episode_title"],
            "ids": sorted(group["ids"]),
            "stored_urls": urls,
            "banks": sorted(group["banks"]),
        }
        if len(urls) != 1:
            listen_multiple_urls.append(base)
            continue
        url = urls[0]
        try:
            body = fetch(url)
            page_title = extract_page_title(body)
        except Exception as exc:
            listen_fetch_errors.append({**base, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not page_title:
            listen_fetch_errors.append({**base, "error": "no page title found"})
            continue
        verified_listen_titles += 1
        if normalize(page_title) != normalize(group["episode_title"]):
            listen_mismatches.append({**base, "page_title": page_title})
        if number % 20 == 0:
            time.sleep(0.1)

    report = {
        "summary": {
            "bank_items": len(bank),
            "unique_bank_episode_titles": len(grouped),
            "all_episode_items": len(all_episode_items),
            "spotify_stored_mismatches": len(spotify_mismatches),
            "spotify_title_unresolved_or_ambiguous": len(spotify_missing),
            "listen_titles_verified": verified_listen_titles,
            "listen_source_mismatches": len(listen_mismatches),
            "listen_multiple_stored_urls_for_same_title": len(listen_multiple_urls),
            "listen_fetch_errors": len(listen_fetch_errors),
        },
        "spotify_mismatches": spotify_mismatches,
        "spotify_missing": spotify_missing,
        "listen_mismatches": listen_mismatches,
        "listen_multiple_urls": listen_multiple_urls,
        "listen_fetch_errors": listen_fetch_errors,
    }

    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={REPORT_PATH}")


if __name__ == "__main__":
    main()
