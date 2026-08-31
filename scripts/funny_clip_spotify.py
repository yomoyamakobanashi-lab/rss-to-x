#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path

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


def _fit_body(text: str, budget: int) -> str:
    """Shorten only at sentence boundaries; never manufacture quote marks."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= budget:
        return clean

    # Keep original punctuation and quote positions. This regex treats a closing
    # Japanese quote after sentence punctuation as part of the same sentence.
    parts = re.findall(r".+?(?:[。！？!?]+[」』]?|$)", clean)
    out = ""
    for part in parts:
        candidate = out + part
        if len(candidate) > budget:
            break
        out = candidate
    if out.strip():
        return out.strip()

    # Last-resort hard cut is quote-safe: do not leave an opening quote dangling.
    clipped = clean[: max(1, budget - 1)].rstrip("、。 ") + "…"
    if clipped.count("「") > clipped.count("」"):
        clipped = clipped.rstrip("…") + "」…"
    if clipped.count("『") > clipped.count("』"):
        clipped = clipped.rstrip("…") + "』…"
    return clipped


def render_root(item: dict) -> str:
    hook = base._hook(item)
    overhead = len(hook) + len(REELPAL_TAG) + 4
    body = _fit_body(item["text"], 280 - overhead)
    root = f"{body}\n\n{hook}\n\n{REELPAL_TAG}"
    if len(root) > 280:
        raise RuntimeError(f"rendered funny clip is too long: {item['id']} ({len(root)})")

    banned = ("面白すぎ", "おもしろすぎ", "好きすぎる", "ずっと聞いてられる")
    if any(word in hook for word in banned):
        raise RuntimeError(f"self-congratulatory funny clip hook rejected: {item['id']} -> {hook}")
    return root


def render_reply(item: dict) -> str:
    target = _exact_spotify_url(item)
    if not target:
        raise RuntimeError(
            f"No verified Spotify episode URL for {item['id']} ({item['source']}); "
            "refusing to publish without an exact /episode/ link."
        )
    return f"この回をSpotifyで👇\n{target}\n\n{REELPAL_TAG}"


_original_load_bank = base.load_bank


def _load_spotify_ready_bank() -> list[dict]:
    full_bank = _original_load_bank()
    ready = [item for item in full_bank if _exact_spotify_url(item)]
    skipped = len(full_bank) - len(ready)
    print(f"[INFO] Spotify-direct funny clips: ready={len(ready)} skipped_without_exact_url={skipped}")
    if not ready:
        raise RuntimeError("No funny clips have a verified Spotify episode URL.")
    return ready


base.load_bank = _load_spotify_ready_bank
base.render_root = render_root
base.render_reply = render_reply


if __name__ == "__main__":
    base.main()
