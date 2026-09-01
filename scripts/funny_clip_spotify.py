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
QUALITY_OVERRIDE_PATHS = [
    ROOT / "data" / "funny_clip_quality_overrides.json",
    ROOT / "data" / "funny_clip_quality_overrides_2.json",
    ROOT / "data" / "funny_clip_quality_overrides_3.json",
]
CANONICAL_BANK_PATHS = [
    ROOT / "data" / "funny_clip_posts_all_episodes.json",
    ROOT / "data" / "funny_clip_legacy_canonical.json",
]
LEGACY_SPOTIFY_OVERRIDE_BY_ID = {
    "legacy-whiplash-oizumi": "archive-whiplash",
    "legacy-godzilla2-biollante": "archive-godzilla2",
    "legacy-fightclub-daiso": "archive-fightclub",
    "legacy-popcorn-koala": "intermission-popcorn",
    "legacy-orangutan-mnemonic": "intermission-orangutan",
    "legacy-omg-kfc": "intermission-godzilla-mouth",
    "legacy-aerosmith-admachi": "intermission-aerosmith",
    "legacy-americanpie-sanity": "intermission-american-pie",
    "legacy-mandalorian-third-person": "intermission-mandalorian",
    "legacy-latest-german": "intermission-conan",
    "legacy-scarymovie-michael-babe": "intermission-goosebumps",
    "legacy-onmyoji-eikogo": "intermission-onmyoji",
    "legacy-kiki-30times": "intermission-kiki",
    "legacy-wicked-wifi": "archive-intermission-wicked-gamera",
    "legacy-ichinose-chaos": "intermission-aetobodm",
    "legacy-terminator1-micarm": "archive-terminator",
    "legacy-terminator3-manuka": "archive-terminator3",
    "legacy-experiment-first": "archive-experiment",
    "legacy-fmj-monhan": "archive-fullmetaljacket",
    "legacy-hellraiser-numbering": "archive-hellraiser",
    "legacy-matrix-kunie": "archive-matrix",
    "legacy-terminator2-frugra": "archive-terminator2",
}
REELPAL_TAG = "#リルパル"

# Production funny clips are one canonical clip per Spotify episode:
# 105 all-episode clips + 22 curated legacy clips. The old archive banks remain
# in the repository for provenance, but are deliberately excluded from posting.
base.BANK_PATHS = CANONICAL_BANK_PATHS


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
    patches: dict[str, dict] = {}
    for path in QUALITY_OVERRIDE_PATHS:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid funny clip quality overrides: {path.name}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"funny clip quality overrides must be a JSON object: {path.name}")
        for clip_id, patch in data.items():
            clip_id = str(clip_id).strip()
            if not clip_id or not isinstance(patch, dict):
                continue
            if clip_id in patches:
                raise RuntimeError(f"duplicate funny clip quality override: {clip_id}")
            patches[clip_id] = patch
    return patches


def _apply_quality_overrides(bank: list[dict]) -> list[dict]:
    patches = _load_quality_overrides()
    if not patches:
        return bank
    by_id = {str(item.get("id") or ""): item for item in bank}
    unknown = sorted(set(patches) - set(by_id))
    if unknown:
        raise RuntimeError("funny clip quality override references unknown ids: " + ", ".join(unknown))
    patched: list[dict] = []
    for item in bank:
        merged = dict(item)
        patch = patches.get(str(item.get("id") or ""))
        if patch:
            merged.update(patch)
        patched.append(merged)
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
    by_title = _spotify_url_by_title(item)
    if by_title:
        return by_title

    overrides = _load_overrides()
    clip_id = str(item.get("id") or "").strip()
    legacy_key = LEGACY_SPOTIFY_OVERRIDE_BY_ID.get(clip_id)
    if legacy_key and legacy_key in overrides:
        return overrides[legacy_key]

    source = str(item.get("source") or "").strip()
    return overrides.get(source)


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
    if len(full_bank) != 127:
        raise RuntimeError(f"canonical funny clip bank must contain exactly 127 clips, got {len(full_bank)}")
    unique_sources = {str(item.get("source") or "") for item in full_bank}
    if len(unique_sources) != 127:
        raise RuntimeError(
            f"canonical funny clip bank must contain 127 unique episode sources, got {len(unique_sources)}"
        )

    missing_by_source: dict[str, str] = {}
    mismatches: list[tuple[str, str, str]] = []
    for item in full_bank:
        if not _exact_spotify_url(item):
            missing_by_source[str(item["source"])] = str(item.get("episode_title") or "")
        mismatch = _stored_spotify_mismatch(item)
        if mismatch:
            mismatches.append((str(item["id"]), mismatch[0], mismatch[1]))
    if missing_by_source:
        details = "\n".join(f"- {source}: {title}" for source, title in sorted(missing_by_source.items()))
        raise RuntimeError(
            "Spotify direct-link coverage is incomplete; no funny clip will be published "
            "until every unique source episode has a verified /episode/ URL.\n" + details
        )
    if mismatches:
        print(f"[WARN] stored Spotify URL mismatches: {len(mismatches)}")
        for clip_id, stored, canonical in mismatches:
            print(f"[WARN] {clip_id}: stored={stored} canonical={canonical}")
    print(f"[OK] canonical funny clip bank: clips={len(full_bank)} unique_sources={len(unique_sources)}")
    print(f"[OK] Spotify direct-link coverage complete: clips={len(full_bank)}")
    return full_bank


base.load_bank = _load_spotify_ready_bank
base.render_reply = render_reply


if __name__ == "__main__":
    base.main()
