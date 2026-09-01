#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import funny_clip_buffer as base

SPOTIFY_EPISODES_PATH = ROOT / "data" / "spotify_episodes.json"
SPOTIFY_OVERRIDES_PATH = ROOT / "data" / "spotify_episode_overrides.json"
QUALITY_OVERRIDES_PATH = ROOT / "data" / "funny_clip_quality_overrides.json"
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


def _load_quality_overrides() -> dict[str, dict]:
    try:
        data = json.loads(QUALITY_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        raise RuntimeError("funny clip quality overrides must be a JSON object")
    return {
        str(clip_id): patch
        for clip_id, patch in data.items()
        if str(clip_id).strip() and isinstance(patch, dict)
    }


def _apply_quality_overrides(bank: list[dict]) -> list[dict]:
    patches = _load_quality_overrides()
    if not patches:
        return bank

    by_id = {str(item.get("id") or ""): item for item in bank}
    unknown = sorted(set(patches) - set(by_id))
    if unknown:
        raise RuntimeError(
            "funny clip quality override references unknown ids: " + ", ".join(unknown)
        )

    patched: list[dict] = []
    for item in bank:
        clip_id = str(item.get("id") or "")
        patch = patches.get(clip_id)
        if patch:
            merged = dict(item)
            merged.update(patch)
            patched.append(merged)
        else:
            patched.append(item)
    return patched


def _spotify_url_by_title(item: dict) -> str | None:
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


def _exact_spotify_url(item: dict) -> str | None:
    # The episode title is the canonical join key. Do not trust a stored
    # spotify_url merely because it has an /episode/ shape: an off-by-one
    # assignment can still point to a perfectly valid but wrong episode.
    by_title = _spotify_url_by_title(item)
    if by_title:
        return by_title

    # Legacy banks use stable, manually verified source keys where the old
    # episode title may not normalize exactly to the current Spotify index.
    source = str(item.get("source") or "").strip()
    override = _load_overrides().get(source)
    if override:
        return override

    return None


def _stored_spotify_mismatch(item: dict) -> tuple[str, str] | None:
    stored = str(item.get("spotify_url") or "").strip()
    if not stored.startswith("https://open.spotify.com/episode/"):
        return None
    canonical = _spotify_url_by_title(item)
    if canonical and stored != canonical:
        return stored, canonical
    return None


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
    full_bank = _apply_quality_overrides(_original_load_bank())
    missing_by_source: dict[str, str] = {}
    mismatches: list[tuple[str, str, str]] = []

    for item in full_bank:
        if not _exact_spotify_url(item):
            missing_by_source[str(item["source"])] = str(item.get("episode_title") or "")
        mismatch = _stored_spotify_mismatch(item)
        if mismatch:
            mismatches.append((str(item["id"]), mismatch[0], mismatch[1]))

    if missing_by_source:
        details = "\n".join(
            f"- {source}: {title}" for source, title in sorted(missing_by_source.items())
        )
        raise RuntimeError(
            "Spotify direct-link coverage is incomplete; no funny clip will be published "
            "until every unique source episode has a verified /episode/ URL.\n" + details
        )

    if mismatches:
        print(f"[WARN] stored Spotify URL mismatches: {len(mismatches)}")
        for clip_id, stored, canonical in mismatches:
            print(f"[WARN] {clip_id}: stored={stored} canonical={canonical}")

    print(f"[OK] Spotify direct-link coverage complete: clips={len(full_bank)}")
    return full_bank


# Keep the quote-aware/conversation-aware root renderer from funny_clip_buffer.
# Only replace bank eligibility and the listener-facing reply with Spotify-direct behavior.
base.load_bank = _load_spotify_ready_bank
base.render_reply = render_reply


if __name__ == "__main__":
    base.main()
