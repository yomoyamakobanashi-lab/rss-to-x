#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

USER_AGENT = "rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"
TIME_RE = re.compile(r"(?<!\d)(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})(?!\d)")
EPISODE_HREF_RE = re.compile(r"^/p/(?P<podcast>[^/]+)/(?P<episode>[a-z0-9]+)$")


@dataclass
class Chapter:
    timestamp: str
    title: str


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_timestamp(ts: str) -> str:
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        m, s = parts
        return f"{m:02d}:{s:02d}"
    if len(parts) == 3:
        h, m, s = parts
        return f"{h:d}:{m:02d}:{s:02d}"
    raise ValueError(f"Unsupported timestamp: {ts}")


def seconds(ts: str) -> int:
    p = [int(x) for x in ts.split(":")]
    if len(p) == 2:
        return p[0] * 60 + p[1]
    return p[0] * 3600 + p[1] * 60 + p[2]


def find_episode_urls(podcast_url: str, limit: int = 12) -> list[str]:
    html = fetch_html(podcast_url)
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if EPISODE_HREF_RE.match(href):
            full = urljoin(podcast_url, href)
            if full not in found:
                found.append(full)
        if len(found) >= limit:
            break
    return found


def episode_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def extract_chapters(episode_url: str) -> tuple[str, list[Chapter]]:
    html = fetch_html(episode_url)
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(
        lambda tag: tag.name in {"h2", "h3"}
        and tag.get_text(" ", strip=True) in {"Table of Contents", "目次"}
    )
    if heading is None:
        return episode_title(soup), []

    chapters: list[Chapter] = []
    for node in heading.find_all_next():
        if node is not heading and node.name in {"h2", "h3"}:
            break
        if node.name != "a":
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        m = TIME_RE.search(text)
        if not m:
            continue
        ts = normalize_timestamp(m.group("time"))
        title = (text[: m.start()] + text[m.end() :]).strip(" -–—()（）\t")
        if not title:
            continue
        if chapters and chapters[-1].timestamp == ts and chapters[-1].title == title:
            continue
        chapters.append(Chapter(ts, title))

    # Spotify requires the first manual chapter to start at 00:00.
    if chapters and seconds(chapters[0].timestamp) != 0:
        chapters[0].timestamp = "00:00"

    return episode_title(soup), chapters


def validate_for_spotify(chapters: list[Chapter]) -> list[str]:
    errors: list[str] = []
    if len(chapters) < 3:
        errors.append("Spotify requires at least 3 chapters.")
    if chapters and seconds(chapters[0].timestamp) != 0:
        errors.append("The first chapter must start at 00:00.")
    for prev, cur in zip(chapters, chapters[1:]):
        if seconds(cur.timestamp) <= seconds(prev.timestamp):
            errors.append(f"Chapter timestamps are not increasing: {prev.timestamp} -> {cur.timestamp}")
        if seconds(cur.timestamp) - seconds(prev.timestamp) < 30:
            errors.append(f"Chapters are less than 30 seconds apart: {prev.timestamp} -> {cur.timestamp}")
    return errors


def spotify_text(chapters: list[Chapter]) -> str:
    return "\n".join(f"{c.timestamp} {c.title}" for c in chapters)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--podcast-url", default="https://listen.style/p/reelpal")
    ap.add_argument("--episode-url")
    ap.add_argument("--scan", type=int, default=12, help="How many recent LISTEN episodes to scan")
    ap.add_argument("--output-dir", default="data/generated_chapters")
    args = ap.parse_args()

    candidates = [args.episode_url] if args.episode_url else find_episode_urls(args.podcast_url, args.scan)
    if not candidates:
        print("No LISTEN episode URLs found", file=sys.stderr)
        return 2

    selected = None
    selected_title = ""
    selected_chapters: list[Chapter] = []
    for url in candidates:
        title, chapters = extract_chapters(url)
        if len(chapters) >= 3:
            selected = url
            selected_title = title
            selected_chapters = chapters
            break

    if not selected:
        print("No recent episode with at least 3 LISTEN chapters found", file=sys.stderr)
        return 3

    errors = validate_for_spotify(selected_chapters)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 4

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    txt = spotify_text(selected_chapters) + "\n"
    (out / "latest.txt").write_text(txt, encoding="utf-8")
    (out / "latest.json").write_text(
        json.dumps(
            {
                "listen_episode_url": selected,
                "title": selected_title,
                "chapters": [asdict(c) for c in selected_chapters],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"LISTEN episode: {selected}")
    print(f"Title: {selected_title}")
    print("\nSpotify-ready chapters:\n")
    print(txt, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
