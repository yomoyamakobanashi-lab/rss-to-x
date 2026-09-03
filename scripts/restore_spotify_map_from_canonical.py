#!/usr/bin/env python3
"""Restore verified Spotify URLs without discarding newer RSS metadata."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MAP_PATH = DATA / "spotify_episodes.json"
CANONICAL_PATHS = [
    DATA / "funny_clip_posts_all_episodes.json",
    DATA / "funny_clip_legacy_canonical.json",
]
OVERRIDES_PATH = DATA / "spotify_episode_overrides.json"

LEGACY_OVERRIDE_KEYS = {
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


def normalize(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    value = value.replace("\u200b", "").replace("\ufeff", "").replace("\ufffc", "")
    value = re.sub(r"[\s　]+", "", value)
    return re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’〜~]", "", value)


def main() -> int:
    episodes = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    verified: dict[str, str] = {}
    for path in CANONICAL_PATHS:
        for item in json.loads(path.read_text(encoding="utf-8")):
            url = str(item.get("spotify_url") or "").strip()
            if not url:
                override_key = LEGACY_OVERRIDE_KEYS.get(str(item.get("id") or ""))
                url = str(overrides.get(override_key) or "").strip()
            title = normalize(item.get("episode_title"))
            if title and url.startswith("https://open.spotify.com/episode/"):
                existing = verified.get(title)
                if existing and existing != url:
                    raise RuntimeError(f"conflicting canonical Spotify URLs for {title}")
                verified[title] = url

    def find_verified(title: object) -> str | None:
        wanted = normalize(title)
        exact = verified.get(wanted)
        if exact:
            return exact
        candidates = {
            url
            for known, url in verified.items()
            if min(len(wanted), len(known)) >= 12 and (wanted in known or known in wanted)
        }
        return next(iter(candidates)) if len(candidates) == 1 else None

    restored = 0
    for episode in episodes:
        if str(episode.get("spotifyUrl") or "").startswith("https://open.spotify.com/episode/"):
            continue
        url = find_verified(episode.get("title"))
        if url:
            episode["spotifyUrl"] = url
            restored += 1

    MAP_PATH.write_text(json.dumps(episodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    covered = sum(
        str(item.get("spotifyUrl") or "").startswith("https://open.spotify.com/episode/")
        for item in episodes
    )
    print(f"[OK] restored={restored}; Spotify map coverage={covered}/{len(episodes)}")
    if covered < 127:
        raise RuntimeError("verified Spotify map coverage fell below 127 episodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
