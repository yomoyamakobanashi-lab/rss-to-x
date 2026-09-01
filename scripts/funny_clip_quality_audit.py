#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
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
LISTEN_BASE = "https://listen.style"
PODCAST_PATH = "/p/reelpal"
USER_AGENT = "rss-to-x funny-clip QA/1.0"


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


def extract_episode_links(page_html: str) -> set[str]:
    links: set[str] = set()
    for match in re.finditer(r'href=["\'](/p/reelpal/[A-Za-z0-9_-]+)["\']', page_html):
        path = match.group(1)
        if path == PODCAST_PATH:
            continue
        links.add(LISTEN_BASE + path)
    return links


def extract_og_title(page_html: str) -> str:
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


def crawl_listen_index(max_pages: int = 20) -> tuple[dict[str, list[dict]], list[str]]:
    urls: set[str] = set()
    page_errors: list[str] = []
    empty_streak = 0

    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"page": page})
        url = f"{LISTEN_BASE}{PODCAST_PATH}?{query}"
        try:
            body = fetch(url)
        except Exception as exc:  # network report, not fatal yet
            page_errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        found = extract_episode_links(body)
        before = len(urls)
        urls.update(found)
        if len(urls) == before:
            empty_streak += 1
        else:
            empty_streak = 0
        if empty_streak >= 2 and page >= 3:
            break

    index: dict[str, list[dict]] = defaultdict(list)
    for number, url in enumerate(sorted(urls), start=1):
        try:
            body = fetch(url)
            title = extract_og_title(body)
        except Exception as exc:
            page_errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        if title:
            index[normalize(title)].append({"title": title, "url": url})
        if number % 20 == 0:
            time.sleep(0.15)

    return dict(index), page_errors


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
        if path.exists():
            chunk = load_json(path)
            if isinstance(chunk, list):
                for item in chunk:
                    copy = dict(item)
                    copy["_bank"] = path.name
                    items.append(copy)
    return items


def main() -> None:
    bank = load_bank_items()
    all_episode_items = load_json(ALL_EPISODES_PATH)
    spotify_index = build_spotify_index()
    listen_index, crawl_errors = crawl_listen_index()

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

    listen_mismatches: list[dict] = []
    listen_missing: list[dict] = []
    listen_ambiguous: list[dict] = []
    for key, group in grouped.items():
        matches = listen_index.get(key, [])
        base = {
            "episode_title": group["episode_title"],
            "ids": sorted(group["ids"]),
            "stored_urls": sorted(group["stored_urls"]),
            "banks": sorted(group["banks"]),
        }
        if not matches:
            listen_missing.append(base)
            continue
        if len(matches) != 1:
            listen_ambiguous.append({**base, "matches": matches})
            continue
        canonical = matches[0]["url"]
        if group["stored_urls"] != {canonical}:
            listen_mismatches.append({**base, "canonical": canonical})

    report = {
        "summary": {
            "bank_items": len(bank),
            "unique_bank_episode_titles": len(grouped),
            "all_episode_items": len(all_episode_items),
            "spotify_index_keys": len(spotify_index),
            "spotify_stored_mismatches": len(spotify_mismatches),
            "spotify_title_unresolved_or_ambiguous": len(spotify_missing),
            "listen_index_keys": len(listen_index),
            "listen_source_mismatches": len(listen_mismatches),
            "listen_title_missing": len(listen_missing),
            "listen_title_ambiguous": len(listen_ambiguous),
            "crawl_errors": len(crawl_errors),
        },
        "spotify_mismatches": spotify_mismatches,
        "spotify_missing": spotify_missing,
        "listen_mismatches": listen_mismatches,
        "listen_missing": listen_missing,
        "listen_ambiguous": listen_ambiguous,
        "crawl_errors": crawl_errors,
    }

    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={REPORT_PATH}")


if __name__ == "__main__":
    main()
