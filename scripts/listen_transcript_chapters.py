#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

USER_AGENT = "rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"
TIME_LINE_RE = re.compile(r"^(?:(?:\d{1,2}):)?\d{1,2}:\d{2}$")


@dataclass
class Segment:
    index: int
    timestamp: str
    text: str


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
        return f"{parts[0]:02d}:{parts[1]:02d}"
    if len(parts) == 3:
        return f"{parts[0]}:{parts[1]:02d}:{parts[2]:02d}"
    raise ValueError(ts)


def seconds(ts: str) -> int:
    p = [int(x) for x in ts.split(":")]
    if len(p) == 2:
        return p[0] * 60 + p[1]
    return p[0] * 3600 + p[1] * 60 + p[2]


def episode_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return " ".join(h1.get_text(" ", strip=True).split())
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return " ".join(str(og["content"]).split())
    return ""


def extract_transcript(url: str) -> tuple[str, list[Segment]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    title = episode_title(soup)
    lines = [" ".join(line.split()) for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]

    # LISTEN transcript timestamps are standalone visible lines such as 00:01,
    # 03:07 or 1:14:10. Build the longest monotonic sequence that begins near
    # the start of the episode, which avoids dates/player duration/UI text.
    markers: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if TIME_LINE_RE.fullmatch(line):
            try:
                markers.append((i, normalize_timestamp(line)))
            except ValueError:
                pass

    if not markers:
        return title, []

    best: list[tuple[int, str]] = []
    for start in range(len(markers)):
        if seconds(markers[start][1]) > 120:
            continue
        seq = [markers[start]]
        last_s = seconds(markers[start][1])
        for item in markers[start + 1 :]:
            cur_s = seconds(item[1])
            if cur_s <= last_s:
                break
            # LISTEN normally emits a marker every few minutes. A gap larger
            # than 25 minutes strongly suggests we left the transcript region.
            if cur_s - last_s > 1500:
                break
            seq.append(item)
            last_s = cur_s
        if len(seq) > len(best):
            best = seq

    if len(best) < 3:
        return title, []

    segments: list[Segment] = []
    for pos, (line_index, ts) in enumerate(best):
        end_index = best[pos + 1][0] if pos + 1 < len(best) else min(len(lines), line_index + 120)
        text_lines = lines[line_index + 1 : end_index]
        # Remove obvious controls that can appear in the rendered page text.
        noise = {
            "Play", "Pause", "Stop", "Copy Link", "Share", "Close",
            "再生", "停止", "リンクをコピー", "シェア",
        }
        text_lines = [x for x in text_lines if x not in noise]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        segments.append(Segment(index=len(segments), timestamp=ts, text=text))

    if segments:
        segments[0].timestamp = "00:00"
    return title, segments


def load_backfill(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("episodes", [])


def strip_json_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def generate_with_gemini(title: str, segments: list[Segment], model: str) -> list[dict]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed") from exc

    transcript_rows = []
    for seg in segments:
        # Enough context for topic identification without bloating a huge show.
        excerpt = seg.text[:5000]
        transcript_rows.append(f"SEGMENT {seg.index} [{seg.timestamp}]\n{excerpt}")

    prompt = f"""You are editing chapter markers for a Japanese movie-discussion podcast.
Episode title: {title}

Below is the LISTEN transcript divided at real timestamp markers. Choose chapter starts by SEGMENT index only.

Requirements:
- Return JSON only: {{"chapters":[{{"segment_index":0,"title":"..."}}, ...]}}
- The first chapter MUST use segment_index 0.
- Choose roughly 5-10 chapters for a normal 60-120 minute episode; fewer for short episodes.
- Prefer genuine topic changes, not every small conversational detour.
- Chapter titles must be concise natural Japanese (roughly 8-28 Japanese characters), concrete, and faithful to what is actually discussed.
- Do not invent topics or facts.
- Avoid generic titles such as 「トーク」「雑談」 when a more specific topic is available.
- Do not return two adjacent chapter starts unless the topic clearly changes.

TRANSCRIPT:

""" + "\n\n".join(transcript_rows)

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    raw = strip_json_fence(response.text or "")
    payload = json.loads(raw)
    requested = payload.get("chapters", payload if isinstance(payload, list) else [])
    if not isinstance(requested, list):
        raise ValueError("Gemini returned an unexpected chapter payload")

    by_index = {seg.index: seg for seg in segments}
    chapters: list[dict] = []
    used: set[int] = set()
    for item in requested:
        try:
            idx = int(item["segment_index"])
            chapter_title = " ".join(str(item["title"]).split()).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if idx not in by_index or idx in used or not chapter_title:
            continue
        used.add(idx)
        chapters.append({
            "segment_index": idx,
            "timestamp": by_index[idx].timestamp,
            "title": chapter_title[:80],
        })

    chapters.sort(key=lambda x: seconds(x["timestamp"]))
    if not chapters or chapters[0]["segment_index"] != 0:
        chapters.insert(0, {"segment_index": 0, "timestamp": "00:00", "title": "オープニング"})

    cleaned: list[dict] = []
    for chapter in chapters:
        if cleaned and seconds(chapter["timestamp"]) - seconds(cleaned[-1]["timestamp"]) < 30:
            continue
        cleaned.append(chapter)

    if len(cleaned) < 3:
        raise ValueError(f"Only {len(cleaned)} valid chapters were generated")
    return cleaned


def probe(episodes: list[dict]) -> int:
    candidates = [e for e in episodes if e.get("status") == "no_chapters"]
    if not candidates:
        print("No no_chapters episodes found to probe.")
        return 0
    ep = candidates[0]
    title, segments = extract_transcript(ep["listen_episode_url"])
    print(f"Probe episode: {ep['listen_episode_url']}")
    print(f"Title: {title}")
    print(f"Transcript segments: {len(segments)}")
    for seg in segments[:5]:
        print(f"[{seg.index}] {seg.timestamp} {seg.text[:240]}")
    if len(segments) < 3:
        print("Transcript extraction failed: fewer than 3 segments", file=sys.stderr)
        return 3
    return 0


def generate(episodes: list[dict], output_dir: Path, limit: int, model: str, delay: float) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [e for e in episodes if e.get("status") == "no_chapters"]
    generated = 0
    errors = 0

    for ep in candidates:
        eid = ep["episode_id"]
        out = output_dir / f"{eid}.json"
        if out.exists():
            continue
        if limit > 0 and generated >= limit:
            break
        try:
            title, segments = extract_transcript(ep["listen_episode_url"])
            if len(segments) < 3:
                raise ValueError(f"Only {len(segments)} transcript segments found")
            chapters = generate_with_gemini(title, segments, model)
            payload = {
                "episode_id": eid,
                "listen_episode_url": ep["listen_episode_url"],
                "spotify_creator_url": ep.get("spotify_creator_url"),
                "title": title or ep.get("title", ""),
                "source": "LISTEN transcript + Gemini",
                "model": model,
                "transcript_segment_count": len(segments),
                "chapters": chapters,
            }
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            generated += 1
            print(f"Generated {eid}: {len(chapters)} chapters — {payload['title']}")
        except Exception as exc:
            errors += 1
            print(f"ERROR {eid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            # A quota/API problem is likely to affect every remaining episode;
            # stop this batch while preserving already-generated files.
            if "429" in str(exc) or "quota" in str(exc).lower() or "rate" in str(exc).lower():
                print("Stopping batch due to API quota/rate limit.", file=sys.stderr)
                break
        if delay > 0:
            time.sleep(delay)

    print(f"AI fallback batch complete: generated={generated}, errors={errors}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", default="data/generated_chapters/backfill.json")
    ap.add_argument("--output-dir", default="data/generated_chapters/ai_backfill")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--model", default="gemini-2.5-flash-lite")
    ap.add_argument("--delay", type=float, default=3.0)
    args = ap.parse_args()

    episodes = load_backfill(Path(args.backfill))
    if args.probe:
        return probe(episodes)
    return generate(episodes, Path(args.output_dir), args.limit, args.model, args.delay)


if __name__ == "__main__":
    raise SystemExit(main())
