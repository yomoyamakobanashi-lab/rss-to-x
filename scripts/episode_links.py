#!/usr/bin/env python3
"""Resolve verified per-episode listening links and render one compact X reply.

LISTEN URLs remain internal source identifiers for transcript QA.  This module
is the only outbound listening-link formatter: Spotify is mandatory, while
Apple Podcasts and YouTube are included only when an exact episode match is in
the checked-in catalogue.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "episode_platform_links.json"
REELPAL_TAG = "#リルパル"

SPOTIFY_RE = re.compile(r"^https://open\.spotify\.com/episode/[A-Za-z0-9]+$")
APPLE_RE = re.compile(
    r"^https://podcasts\.apple\.com/jp/podcast/id1810778208\?i=\d+$"
)
YOUTUBE_RE = re.compile(r"^https://youtu\.be/[A-Za-z0-9_-]{11}$")
URL_RE = re.compile(r"https?://[^\s\)\]\}<>]+")


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("\ufffc", "").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠ー]+", "", text)


def _valid_row(row: dict) -> bool:
    if not str(row.get("guid") or "").strip():
        return False
    if not normalize_title(row.get("title")):
        return False
    if not SPOTIFY_RE.fullmatch(str(row.get("spotify_url") or "")):
        return False
    apple = str(row.get("apple_url") or "")
    youtube = str(row.get("youtube_url") or "")
    if apple and not APPLE_RE.fullmatch(apple):
        return False
    if youtube and not YOUTUBE_RE.fullmatch(youtube):
        return False
    listen_urls = row.get("listen_urls") or []
    return isinstance(listen_urls, list) and all(
        str(url).startswith("https://listen.style/p/reelpal/") for url in listen_urls
    )


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError("verified episode platform catalogue is unavailable") from exc
    if not isinstance(data, list) or not data:
        raise RuntimeError("episode platform catalogue must be a non-empty JSON array")
    if not all(isinstance(row, dict) and _valid_row(row) for row in data):
        raise RuntimeError("episode platform catalogue contains an invalid or unverified URL")
    guids = [str(row["guid"]) for row in data]
    spotify_urls = [str(row["spotify_url"]) for row in data]
    if len(set(guids)) != len(guids) or len(set(spotify_urls)) != len(spotify_urls):
        raise RuntimeError("episode platform catalogue contains duplicate episode identities")
    for key in ("apple_url", "youtube_url"):
        values = [str(row.get(key)) for row in data if row.get(key)]
        if len(set(values)) != len(values):
            raise RuntimeError(f"episode platform catalogue contains duplicate {key}")
    listen_urls = [str(url) for row in data for url in row.get("listen_urls", [])]
    if len(set(listen_urls)) != len(listen_urls):
        raise RuntimeError("one LISTEN source identity maps to multiple platform episodes")
    return tuple(data)


def _unique(rows: list[dict], reason: str) -> dict | None:
    by_guid = {str(row["guid"]): row for row in rows}
    if len(by_guid) > 1:
        raise RuntimeError(f"ambiguous episode platform match by {reason}")
    return next(iter(by_guid.values()), None)


def resolve_episode_links(
    *,
    title: object = "",
    guid: object = "",
    listen_url: object = "",
    spotify_url: object = "",
) -> dict:
    """Return one verified catalogue row; never guess a listening destination."""
    rows = list(load_catalog())
    wanted_spotify = str(spotify_url or "").split("?", 1)[0].strip()
    if wanted_spotify:
        match = _unique(
            [row for row in rows if row["spotify_url"] == wanted_spotify], "Spotify URL"
        )
        if match:
            return dict(match)

    wanted_guid = str(guid or "").strip()
    if wanted_guid:
        match = _unique([row for row in rows if row["guid"] == wanted_guid], "GUID")
        if match:
            return dict(match)

    wanted_listen = str(listen_url or "").split("?", 1)[0].rstrip("/").strip()
    if wanted_listen:
        match = _unique(
            [row for row in rows if wanted_listen in row.get("listen_urls", [])],
            "internal LISTEN identity",
        )
        if match:
            return dict(match)

    wanted_title = normalize_title(title)
    if wanted_title:
        exact = _unique(
            [row for row in rows if normalize_title(row["title"]) == wanted_title],
            "normalized title",
        )
        if exact:
            return dict(exact)
        contained = [
            row
            for row in rows
            if min(len(wanted_title), len(normalize_title(row["title"]))) >= 18
            and (
                wanted_title.startswith(normalize_title(row["title"]))
                or normalize_title(row["title"]).startswith(wanted_title)
            )
        ]
        match = _unique(contained, "long title prefix")
        if match:
            return dict(match)

    raise RuntimeError(
        "No verified Spotify episode URL for the requested episode; refusing to publish a fallback link"
    )


def x_length(text: str) -> int:
    total = 0
    last = 0
    for match in URL_RE.finditer(text):
        segment = text[last : match.start()]
        total += sum(1 if ord(char) <= 0x7F else 2 for char in segment)
        total += 23
        last = match.end()
    total += sum(1 if ord(char) <= 0x7F else 2 for char in text[last:])
    return total


def render_episode_reply(
    *,
    title: object = "",
    guid: object = "",
    listen_url: object = "",
    spotify_url: object = "",
    intro: str = "🎧 この回を聴く",
) -> str:
    links = resolve_episode_links(
        title=title,
        guid=guid,
        listen_url=listen_url,
        spotify_url=spotify_url,
    )
    lines = [str(intro).strip(), "Spotify", links["spotify_url"]]
    if links.get("apple_url"):
        lines.extend(["Apple Podcasts", links["apple_url"]])
    if links.get("youtube_url"):
        lines.extend(["YouTube", links["youtube_url"]])
    lines.extend(["", REELPAL_TAG])
    reply = "\n".join(lines)
    if "listen.style" in reply.lower():
        raise RuntimeError("LISTEN must never appear in a listener-facing reply")
    # Buffer also performs a raw 280-character guard before X shortens URLs.
    if len(reply) > 280 or x_length(reply) > 280:
        raise RuntimeError("multi-platform episode reply exceeds X limit")
    return reply


def validate_catalog() -> dict[str, int]:
    rows = load_catalog()
    return {
        "episodes": len(rows),
        "spotify": sum(bool(row.get("spotify_url")) for row in rows),
        "apple": sum(bool(row.get("apple_url")) for row in rows),
        "youtube": sum(bool(row.get("youtube_url")) for row in rows),
    }
