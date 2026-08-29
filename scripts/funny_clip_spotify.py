#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from scripts import funny_clip_buffer as base

ROOT = Path(__file__).resolve().parents[1]
SPOTIFY_EPISODES_PATH = ROOT / "data" / "spotify_episodes.json"
SPOTIFY_OVERRIDES_PATH = ROOT / "data" / "spotify_episode_overrides.json"
REELPAL_TAG = "#リルパル"


def _normalize(value: str) -> str:
    value = str(value or "").lower()
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]]", "", value)
    return value


def _load_spotify_index() -> list[dict]:
    try:
        data = json.loads(SPOTIFY_EPISODES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _load_overrides() -> dict[str, str]:
    try:
        data = json.loads(SPOTIFY_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in data.items()
        if str(value).strip().startswith("https://open.spotify.com/episode/")
    }


def _exact_spotify_url(item: dict) -> str | None:
    explicit = str(item.get("spotify_url") or "").strip()
    if explicit.startswith("https://open.spotify.com/episode/"):
        return explicit

    source = str(item.get("source") or "").strip()
    override = _load_overrides().get(source)
    if override:
        return override

    wanted = _normalize(item.get("episode_title", ""))
    if not wanted:
        return None

    for episode in _load_spotify_index():
        url = str(episode.get("spotifyUrl") or "").strip()
        if not url.startswith("https://open.spotify.com/episode/"):
            continue
        candidates = {
            _normalize(episode.get("title", "")),
            _normalize(episode.get("normalizedTitle", "")),
        }
        if wanted in candidates:
            return url

    return None


def _search_hint(item: dict) -> str:
    title = str(item.get("episode_title") or "")

    hashtag = re.search(r"#([^\s　とか、，。…]+)", title)
    if hashtag:
        hint = hashtag.group(1).strip()
    else:
        quoted = re.search(r"[『「](.*?)[』」]", title)
        hint = quoted.group(1).strip() if quoted else str(item.get("topic") or "").strip()

    hint = re.sub(r"\s+", " ", hint).strip()
    return hint[:18] or "リルパル"


def _spotify_target(item: dict) -> tuple[str, bool]:
    exact = _exact_spotify_url(item)
    if exact:
        return exact, True

    query = f"Reel Friends TOKYO {_search_hint(item)}"
    return f"https://open.spotify.com/search/{quote(query, safe='')}/episodes", False


def render_reply(item: dict) -> str:
    target, exact = _spotify_target(item)
    lead = "この回をSpotifyで👇" if exact else "この回をSpotifyで探す👇"
    return f"{lead}\n{target}\n\n{REELPAL_TAG}"


# Keep LISTEN URL/timestamp inside the bank as source evidence, but make public
# replies Spotify-first. base.main resolves render_reply dynamically, so replacing
# the module global is enough to preserve the existing rotation/posting behavior.
base.render_reply = render_reply


if __name__ == "__main__":
    base.main()
