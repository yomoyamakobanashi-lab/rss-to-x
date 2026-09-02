#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

USER_AGENT = "rss-to-x/1.1 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"
TIME_RE = re.compile(r"(?<!\d)(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})(?!\d)")
EPISODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


@dataclass
class Chapter:
    timestamp: str
    title: str


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.7",
        },
    )
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


def canonical_episode_url(candidate: str, podcast_url: str) -> str | None:
    """Turn LISTEN href variants into one canonical episode URL."""
    if not candidate:
        return None

    base = urlparse(podcast_url)
    base_path = base.path.rstrip("/")
    prefix = base_path + "/"

    # LISTEN may emit relative, absolute, query-bearing or escaped links.
    candidate = unescape(candidate).replace("\\/", "/").replace("\\u002F", "/")
    full = urljoin(podcast_url.rstrip("/") + "/", candidate)
    parsed = urlparse(full)

    if parsed.netloc != base.netloc:
        return None

    path = parsed.path.rstrip("/")
    if not path.startswith(prefix):
        return None

    episode_id = path[len(prefix):]
    if "/" in episode_id or not EPISODE_ID_RE.fullmatch(episode_id):
        return None

    return f"{base.scheme}://{base.netloc}{prefix}{episode_id}"


def find_episode_urls(podcast_url: str, limit: int = 12) -> list[str]:
    html = fetch_html(podcast_url)
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []

    def add_candidate(candidate: str) -> None:
        full = canonical_episode_url(candidate, podcast_url)
        if full and full not in found:
            found.append(full)

    # Normal anchors first.
    for a in soup.find_all("a", href=True):
        add_candidate(str(a["href"]))
        if len(found) >= limit:
            return found

    # Fallback for links embedded in framework JSON/escaped HTML.
    base = urlparse(podcast_url)
    prefix = base.path.rstrip("/") + "/"
    normalized_html = unescape(html).replace("\\/", "/").replace("\\u002F", "/")
    pattern = re.compile(re.escape(prefix) + r"[A-Za-z0-9_-]{6,}")
    for match in pattern.finditer(normalized_html):
        add_candidate(match.group(0))
        if len(found) >= limit:
            break

    return found


def episode_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def _chapter_from_text(text: str) -> Chapter | None:
    text = " ".join(text.split())
    m = TIME_RE.search(text)
    if not m:
        return None

    ts = normalize_timestamp(m.group("time"))
    title = (text[: m.start()] + text[m.end() :]).strip(" -–—()（）\t")
    if not title:
        return None
    return Chapter(ts, title)


def extract_chapters(episode_url: str) -> tuple[str, list[Chapter]]:
    html = fetch_html(episode_url)
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(
        lambda tag: tag.name in {"h2", "h3"}
        and " ".join(tag.get_text(" ", strip=True).split()).casefold()
        in {"table of contents", "目次"}
    )
    if heading is None:
        return episode_title(soup), []

    chapters: list[Chapter] = []

    # LISTEN's ToC entries are links immediately after the ToC heading.
    for node in heading.find_all_next():
        if node is not heading and node.name in {"h2", "h3"}:
            break
        if node.name != "a":
            continue

        chapter = _chapter_from_text(node.get_text(" ", strip=True))
        if chapter is None:
            continue

        key = (chapter.timestamp, chapter.title)
        if any((c.timestamp, c.title) == key for c in chapters):
            continue
        chapters.append(chapter)

        # LISTEN currently generates a compact ToC; cap protects us from
        # accidentally consuming timestamped transcript controls after it.
        if len(chapters) >= 30:
            break

    # Fallback: when LISTEN changes the ToC markup, inspect the closest
    # following container and collect timestamped text elements.
    if len(chapters) < 3:
        chapters = []
        parent = heading.parent
        if parent is not None:
            for node in parent.find_all(["a", "li", "p"], recursive=True):
                chapter = _chapter_from_text(node.get_text(" ", strip=True))
                if chapter is None:
                    continue
                key = (chapter.timestamp, chapter.title)
                if any((c.timestamp, c.title) == key for c in chapters):
                    continue
                chapters.append(chapter)
                if len(chapters) >= 30:
                    break

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
            errors.append(
                f"Chapter timestamps are not increasing: {prev.timestamp} -> {cur.timestamp}"
            )
        if seconds(cur.timestamp) - seconds(prev.timestamp) < 30:
            errors.append(
                f"Chapters are less than 30 seconds apart: {prev.timestamp} -> {cur.timestamp}"
            )
    return errors


def spotify_text(chapters: list[Chapter]) -> str:
    return "\n".join(f"{c.timestamp} {c.title}" for c in chapters)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--podcast-url", default="https://listen.style/p/reelpal")
    ap.add_argument("--episode-url")
    ap.add_argument(
        "--scan",
        type=int,
        default=12,
        help="How many recent LISTEN episodes to scan",
    )
    ap.add_argument("--output-dir", default="data/generated_chapters")
    args = ap.parse_args()

    candidates = (
        [args.episode_url]
        if args.episode_url
        else find_episode_urls(args.podcast_url, args.scan)
    )
    if not candidates:
        print(
            f"No LISTEN episode URLs found on {args.podcast_url}",
            file=sys.stderr,
        )
        return 2

    print(f"Found {len(candidates)} LISTEN episode candidate(s).")

    selected = None
    selected_title = ""
    selected_chapters: list[Chapter] = []
    for url in candidates:
        title, chapters = extract_chapters(url)
        print(f"Checked {url}: {len(chapters)} chapter(s)")
        if len(chapters) >= 3:
            selected = url
            selected_title = title
            selected_chapters = chapters
            break

    if not selected:
        print(
            "No recent episode with at least 3 LISTEN chapters found",
            file=sys.stderr,
        )
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
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"LISTEN episode: {selected}")
    print(f"Title: {selected_title}")
    print("\nSpotify-ready chapters:\n")
    print(txt, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
