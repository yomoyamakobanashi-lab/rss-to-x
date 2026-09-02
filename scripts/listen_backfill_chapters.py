#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

USER_AGENT = "rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"
TIME_RE = re.compile(r"(?<!\d)(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})(?!\d)")
EPISODE_PATH_RE = re.compile(r"^/p/(?P<podcast>[^/?#]+)/(?P<episode>[A-Za-z0-9_-]+)/?$")


@dataclass
class Chapter:
    timestamp: str
    title: str


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
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
    if len(p) == 3:
        return p[0] * 3600 + p[1] * 60 + p[2]
    raise ValueError(f"Unsupported timestamp: {ts}")


def podcast_slug(podcast_url: str) -> str:
    parts = urlsplit(podcast_url).path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "p":
        raise ValueError(f"Unexpected LISTEN podcast URL: {podcast_url}")
    return parts[1]


def page_url(podcast_url: str, page: int) -> str:
    parsed = urlsplit(podcast_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["sort"] = "oldest"
    query["page"] = str(page)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def canonical_episode_url(podcast_url: str, href: str) -> str | None:
    full = urljoin(podcast_url, href.strip())
    parsed = urlsplit(full)
    if parsed.netloc not in {"listen.style", "www.listen.style"}:
        return None
    m = EPISODE_PATH_RE.match(parsed.path)
    if not m or m.group("podcast") != podcast_slug(podcast_url):
        return None
    return f"https://listen.style/p/{m.group('podcast')}/{m.group('episode')}"


def find_all_episode_urls(podcast_url: str, max_pages: int = 50) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        url = page_url(podcast_url, page)
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        page_urls: list[str] = []

        for a in soup.find_all("a", href=True):
            episode_url = canonical_episode_url(podcast_url, a["href"])
            if episode_url and episode_url not in page_urls:
                page_urls.append(episode_url)

        new_urls = [u for u in page_urls if u not in seen]
        print(f"LISTEN page {page}: {len(page_urls)} episode link(s), {len(new_urls)} new")

        if not page_urls:
            break
        if not new_urls:
            break

        for u in new_urls:
            seen.add(u)
            found.append(u)

    return found


def episode_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return " ".join(h1.get_text(" ", strip=True).split())
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return " ".join(str(og["content"]).split())
    return ""


def extract_chapters(episode_url: str) -> tuple[str, list[Chapter]]:
    html = fetch_html(episode_url)
    soup = BeautifulSoup(html, "html.parser")
    title = episode_title(soup)

    heading = soup.find(
        lambda tag: tag.name in {"h2", "h3", "h4"}
        and " ".join(tag.get_text(" ", strip=True).split()) in {"Table of Contents", "目次"}
    )
    if heading is None:
        return title, []

    chapters: list[Chapter] = []
    for node in heading.find_all_next():
        if node is not heading and node.name in {"h2", "h3", "h4"}:
            break
        if node.name != "a":
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        m = TIME_RE.search(text)
        if not m:
            continue
        ts = normalize_timestamp(m.group("time"))
        chapter_title = (text[: m.start()] + text[m.end() :]).strip(" -–—()（）\t")
        if not chapter_title:
            continue
        if chapters and chapters[-1].timestamp == ts and chapters[-1].title == chapter_title:
            continue
        chapters.append(Chapter(ts, chapter_title))

    return title, chapters


def sanitize_for_spotify(chapters: list[Chapter]) -> tuple[list[Chapter], int]:
    """Normalize LISTEN chapters to Spotify manual-chapter constraints.

    Spotify requires the first chapter at 00:00 and chapter starts at least
    30 seconds apart. LISTEN can occasionally emit several headings only a
    few seconds apart. Keep the earliest heading in each 30-second window and
    drop the later near-duplicate boundary rather than shifting timestamps and
    making them inaccurate.
    """
    if not chapters:
        return [], 0

    cleaned: list[Chapter] = []
    dropped = 0
    for idx, chapter in enumerate(chapters):
        candidate = Chapter(chapter.timestamp, chapter.title)
        if idx == 0:
            candidate.timestamp = "00:00"
            cleaned.append(candidate)
            continue

        delta = seconds(candidate.timestamp) - seconds(cleaned[-1].timestamp)
        if delta < 30:
            dropped += 1
            continue
        cleaned.append(candidate)

    return cleaned, dropped


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


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = value.replace("🎧", " ")
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[#＃]", "", value)
    value = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]+", "", value)
    return value


def read_spotify_episode_index(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    records: list[dict[str, str]] = []
    current: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("http://") or line.startswith("https://"):
            if current:
                title = " ".join(current)
                records.append({"title": title.removeprefix("🎧").strip(), "url": line})
                current = []
            continue
        if line.startswith("🎧") and current:
            current = [line]
        else:
            current.append(line)
    return records


def match_spotify_url(title: str, records: list[dict[str, str]]) -> tuple[str | None, float]:
    needle = normalize_title(title)
    if not needle or not records:
        return None, 0.0

    best_url: str | None = None
    best_score = 0.0
    for rec in records:
        candidate = normalize_title(rec["title"])
        if not candidate:
            continue
        if candidate == needle:
            return rec["url"], 1.0
        score = SequenceMatcher(None, needle, candidate).ratio()
        if min(len(needle), len(candidate)) >= 16 and (needle in candidate or candidate in needle):
            score = max(score, 0.92)
        if score > best_score:
            best_score = score
            best_url = rec["url"]

    if best_score < 0.62:
        return None, best_score
    return best_url, best_score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--podcast-url", default="https://listen.style/p/reelpal")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--episodes-index", default="episodes.txt")
    ap.add_argument("--output", default="data/generated_chapters/backfill.json")
    ap.add_argument("--delay", type=float, default=0.15, help="Delay between LISTEN episode requests")
    args = ap.parse_args()

    urls = find_all_episode_urls(args.podcast_url, args.max_pages)
    if not urls:
        print("No LISTEN episodes found", file=sys.stderr)
        return 2

    spotify_records = read_spotify_episode_index(Path(args.episodes_index))
    print(f"Discovered {len(urls)} LISTEN episode(s); Spotify index has {len(spotify_records)} record(s).")

    results: list[dict] = []
    for idx, url in enumerate(urls, 1):
        try:
            title, source_chapters = extract_chapters(url)
            chapters, dropped = sanitize_for_spotify(source_chapters)
            errors = validate_for_spotify(chapters) if chapters else []
            if not source_chapters:
                status = "no_chapters"
            elif errors:
                status = "invalid"
            else:
                status = "ready"
            spotify_url, match_score = match_spotify_url(title, spotify_records)
            result = {
                "episode_id": url.rstrip("/").split("/")[-1],
                "listen_episode_url": url,
                "title": title,
                "status": status,
                "errors": errors,
                "source_chapter_count": len(source_chapters),
                "chapters_dropped_for_spotify": dropped,
                "chapters": [asdict(c) for c in chapters],
                "spotify_creator_url": spotify_url,
                "spotify_match_score": round(match_score, 3),
            }
            results.append(result)
            print(
                f"[{idx}/{len(urls)}] {status:11} chapters={len(chapters):2d} "
                f"dropped={dropped:2d} spotify={'yes' if spotify_url else 'no '} {title[:80]}"
            )
        except Exception as exc:
            results.append(
                {
                    "episode_id": url.rstrip("/").split("/")[-1],
                    "listen_episode_url": url,
                    "title": "",
                    "status": "error",
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "source_chapter_count": 0,
                    "chapters_dropped_for_spotify": 0,
                    "chapters": [],
                    "spotify_creator_url": None,
                    "spotify_match_score": 0.0,
                }
            )
            print(f"[{idx}/{len(urls)}] ERROR {url}: {exc}", file=sys.stderr)
        if args.delay > 0:
            time.sleep(args.delay)

    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    payload = {
        "podcast_url": args.podcast_url,
        "episode_count": len(results),
        "counts": counts,
        "spotify_links_matched": sum(1 for x in results if x.get("spotify_creator_url")),
        "episodes": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("Backfill scan complete:")
    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(f"Spotify links matched: {payload['spotify_links_matched']}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
