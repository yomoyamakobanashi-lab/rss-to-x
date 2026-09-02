#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

RSS_URL = "https://anchor.fm/s/10422ca68/podcast/rss"
TARGET_EPISODE_ID = "lt7ujaek"
TARGET_TITLE_HINT = "みんなおしゃべり"
OUTPUT = Path(f"data/generated_chapters/ai_backfill/{TARGET_EPISODE_ID}.json")
BACKFILL = Path("data/generated_chapters/backfill.json")
USER_AGENT = "rss-to-x/1.2 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"
MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
TS_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


def fetch_bytes(url: str) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=120) as response:
        content_type = (response.headers.get_content_type() or "application/octet-stream").lower()
        return response.read(), content_type


def find_rss_audio() -> tuple[str, str, str]:
    xml_bytes, _ = fetch_bytes(RSS_URL)
    root = ET.fromstring(xml_bytes)
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        if TARGET_TITLE_HINT not in title:
            continue
        enclosure = item.find("enclosure")
        if enclosure is None:
            raise RuntimeError("Matched RSS item has no enclosure")
        audio_url = html.unescape(enclosure.attrib.get("url", "")).strip()
        mime = (enclosure.attrib.get("type") or "audio/mpeg").strip()
        if not audio_url:
            raise RuntimeError("Matched RSS item has empty enclosure URL")
        return title, audio_url, mime
    raise RuntimeError(f"Could not find RSS episode containing: {TARGET_TITLE_HINT}")


def to_seconds(ts: str) -> int:
    m = TS_RE.fullmatch(ts.strip())
    if not m:
        raise ValueError(f"Invalid timestamp: {ts}")
    h = int(m.group(1) or 0)
    minute = int(m.group(2))
    sec = int(m.group(3))
    return h * 3600 + minute * 60 + sec


def normalize_ts(ts: str) -> str:
    total = to_seconds(ts)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def strip_json_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_chapters(raw: str) -> list[dict]:
    payload = json.loads(strip_json_fence(raw))
    rows = payload.get("chapters", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        raise ValueError("Unexpected chapter JSON")

    chapters: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("timestamp", "")).strip().strip("()")
        title = " ".join(str(row.get("title", "")).split()).strip()
        if not ts or not title:
            continue
        try:
            normalized = normalize_ts(ts)
        except ValueError:
            continue
        chapters.append({"timestamp": normalized, "title": title[:80]})

    chapters.sort(key=lambda c: to_seconds(c["timestamp"]))
    if not chapters or to_seconds(chapters[0]["timestamp"]) != 0:
        chapters.insert(0, {"timestamp": "00:00", "title": "オープニング"})
    else:
        chapters[0]["timestamp"] = "00:00"

    cleaned: list[dict] = []
    for ch in chapters:
        if cleaned and to_seconds(ch["timestamp"]) - to_seconds(cleaned[-1]["timestamp"]) < 30:
            continue
        cleaned.append(ch)

    # For this 31-minute intermission, 4-8 useful chapters is enough.
    if len(cleaned) > 8:
        # Keep the first and spread the remaining selections across the episode.
        first = cleaned[0]
        rest = cleaned[1:]
        slots = 7
        if len(rest) > slots:
            picks = []
            for i in range(slots):
                idx = round(i * (len(rest) - 1) / max(1, slots - 1))
                if rest[idx] not in picks:
                    picks.append(rest[idx])
            cleaned = [first] + picks
    if len(cleaned) < 3:
        raise ValueError(f"Only {len(cleaned)} usable chapters returned")
    return cleaned


def load_spotify_url() -> str | None:
    data = json.loads(BACKFILL.read_text(encoding="utf-8"))
    for ep in data.get("episodes", []):
        if ep.get("episode_id") == TARGET_EPISODE_ID:
            return ep.get("spotify_creator_url")
    return None


def generate_from_audio(audio_path: str, title: str) -> tuple[list[dict], str]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    uploaded = client.files.upload(file=audio_path)
    try:
        # Audio generally becomes usable immediately; poll briefly if the backend reports a state.
        for _ in range(24):
            state = getattr(getattr(uploaded, "state", None), "name", None)
            if not state or state == "ACTIVE":
                break
            if state == "FAILED":
                raise RuntimeError("Gemini audio upload processing failed")
            time.sleep(5)
            uploaded = client.files.get(name=uploaded.name)

        prompt = f"""Analyze this Japanese podcast episode directly from its audio and create Spotify chapter markers.
Episode title: {title}

Return JSON only in this exact shape:
{{"chapters":[{{"timestamp":"00:00","title":"..."}}, ...]}}

Requirements:
- The first chapter MUST start at 00:00.
- This episode is about 31 minutes long. Choose 4 to 7 chapters total.
- Use the actual audio timeline. Put each timestamp at a genuine topic transition or clear segment start.
- Keep chapter starts at least 30 seconds apart.
- Titles must be concise, natural Japanese, about 8-28 Japanese characters when practical.
- Prefer specific topics actually discussed (for example the named films/people/topics) over generic labels like 「雑談」.
- Do not invent facts, topics, quotes, or timestamps unsupported by the audio.
- Do not include an end marker after the episode ends.
"""

        failures: list[str] = []
        for model in MODELS:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[prompt, uploaded],
                        config=types.GenerateContentConfig(response_mime_type="application/json"),
                    )
                    return parse_chapters(response.text or ""), model
                except Exception as exc:
                    failures.append(f"{model}: {type(exc).__name__}: {str(exc)[:240]}")
                    if attempt == 0:
                        time.sleep(3)
            print(f"Model {model} failed; trying fallback", file=sys.stderr)
        raise RuntimeError("All audio models failed: " + " | ".join(failures[-6:]))
    finally:
        try:
            if getattr(uploaded, "name", None):
                client.files.delete(name=uploaded.name)
        except Exception:
            pass


def main() -> int:
    if OUTPUT.exists():
        print(f"Already rescued: {OUTPUT}")
        return 0

    title, audio_url, rss_mime = find_rss_audio()
    print(f"Matched RSS episode: {title}")
    print(f"Audio URL host: {urlparse(audio_url).netloc}; RSS MIME={rss_mime}")

    audio_bytes, fetched_mime = fetch_bytes(audio_url)
    if len(audio_bytes) < 100_000:
        raise RuntimeError(f"Downloaded audio is unexpectedly small: {len(audio_bytes)} bytes")
    suffix = ".m4a" if "mp4" in (fetched_mime or rss_mime) else ".mp3"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        audio_path = f.name
    try:
        print(f"Downloaded audio: {len(audio_bytes) / 1024 / 1024:.1f} MiB")
        chapters, model = generate_from_audio(audio_path, title)
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "episode_id": TARGET_EPISODE_ID,
        "listen_episode_url": f"https://listen.style/p/reelpal/{TARGET_EPISODE_ID}",
        "spotify_creator_url": load_spotify_url(),
        "title": title,
        "source": "Spotify RSS audio + Gemini audio understanding",
        "model": model,
        "chapters": chapters,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
