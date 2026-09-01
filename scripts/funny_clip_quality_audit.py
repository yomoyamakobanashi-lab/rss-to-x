#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORT_PATH = ROOT / "funny_clip_quality_report.json"
BASE_EPISODE_COVERAGE = 127

BANK_PATHS = [
    DATA / "funny_clip_posts_all_episodes.json",
    DATA / "funny_clip_legacy_canonical.json",
    *sorted(DATA.glob("funny_clip_extras*.json")),
]
SPOTIFY_EPISODES_PATH = DATA / "spotify_episodes.json"
SPOTIFY_OVERRIDES_PATH = DATA / "spotify_episode_overrides.json"
USER_AGENT = "Mozilla/5.0 (compatible; ReelPalFunnyClipQA/2.2)"

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


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).lower()
    value = value.replace("\u200b", "").replace("\ufeff", "").replace("\ufffc", "")
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’]", "", value)
    return value


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_quality_overrides() -> dict[str, dict]:
    patches: dict[str, dict] = {}
    for path in sorted(DATA.glob("funny_clip_quality_overrides*.json")):
        data = load_json(path)
        if not isinstance(data, dict):
            raise RuntimeError(f"quality override is not an object: {path.name}")
        for clip_id, patch in data.items():
            if not isinstance(patch, dict):
                continue
            clip_id = str(clip_id)
            if clip_id in patches:
                raise RuntimeError(f"duplicate quality override id: {clip_id}")
            patches[clip_id] = patch
    return patches


def load_canonical_bank() -> list[dict]:
    patches = load_quality_overrides()
    bank: list[dict] = []
    for path in BANK_PATHS:
        chunk = load_json(path)
        if not isinstance(chunk, list):
            raise RuntimeError(f"canonical bank invalid: {path.name}")
        for original in chunk:
            item = dict(original)
            item.update(patches.get(str(item.get("id") or ""), {}))
            item["_bank"] = path.name
            bank.append(item)
    unknown = sorted(set(patches) - {str(x.get("id") or "") for x in bank})
    if unknown:
        raise RuntimeError("quality overrides reference unknown canonical ids: " + ", ".join(unknown))
    return bank


def fetch(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def visible_text(page_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", page_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<svg\b[^>]*>.*?</svg>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|h[1-6]|section|article|main|button)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_listen_suffix(title: str) -> str:
    title = html.unescape(str(title or "")).strip()
    title = re.sub(r"\s+-\s+【#リルパル】.*$", "", title).strip()
    title = re.sub(r"\s+-\s+LISTEN\s*$", "", title, flags=re.I).strip()
    return title


def extract_page_title(page_html: str) -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return _strip_listen_suffix(match.group(1))
    match = re.search(r"<title>(.*?)</title>", page_html, flags=re.I | re.S)
    if match:
        return _strip_listen_suffix(re.sub(r"\s+", " ", match.group(1)))
    return ""


def build_spotify_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for episode in load_json(SPOTIFY_EPISODES_PATH):
        url = str(episode.get("spotifyUrl") or "").strip()
        if not url.startswith("https://open.spotify.com/episode/"):
            continue
        for title in (episode.get("title", ""), episode.get("normalizedTitle", "")):
            key = normalize(title)
            if key and url not in index[key]:
                index[key].append(url)
    return dict(index)


def load_spotify_overrides() -> dict[str, str]:
    data = load_json(SPOTIFY_OVERRIDES_PATH)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if str(v).startswith("https://open.spotify.com/episode/")}


def resolve_spotify(item: dict, index: dict[str, list[str]], overrides: dict[str, str]) -> str | None:
    matches = index.get(normalize(item.get("episode_title", "")), [])
    if len(matches) == 1:
        return matches[0]
    key = LEGACY_SPOTIFY_OVERRIDE_BY_ID.get(str(item.get("id") or ""))
    if key:
        return overrides.get(key)
    return None


def _same_listen_title(expected: str, actual: str) -> bool:
    wanted = normalize(expected)
    page = normalize(actual)
    if wanted == page:
        return True
    shorter, longer = sorted((wanted, page), key=len)
    return len(shorter) >= 12 and shorter in longer


def verify_listen(item: dict) -> tuple[str, dict]:
    base = {
        "id": item.get("id"),
        "episode_title": item.get("episode_title"),
        "source_url": item.get("source_url"),
    }
    url = str(item.get("source_url") or "")
    if not url.startswith("https://listen.style/p/reelpal/"):
        return "error", {**base, "error": "invalid LISTEN URL shape"}
    try:
        page_html = fetch(url)
        page_title = extract_page_title(page_html)
    except Exception as exc:
        return "error", {**base, "error": f"{type(exc).__name__}: {exc}"}
    if not page_title:
        return "error", {**base, "error": "no page title found"}
    if not _same_listen_title(str(item.get("episode_title") or ""), page_title):
        return "mismatch", {**base, "page_title": page_title}

    if item.get("parent_id"):
        page_text = normalize(visible_text(page_html))
        missing_turns = []
        for turn in item.get("dialogue", []):
            wanted = normalize(turn)
            if wanted and wanted not in page_text:
                missing_turns.append(turn)
        if missing_turns:
            return "dialogue_mismatch", {**base, "missing_turns": missing_turns}

    return "ok", base


def main() -> None:
    bank = load_canonical_bank()
    if len(bank) < BASE_EPISODE_COVERAGE:
        raise RuntimeError(
            f"canonical audit requires at least {BASE_EPISODE_COVERAGE} clips, got {len(bank)}"
        )

    ids = [str(x.get("id") or "") for x in bank]
    sources = [str(x.get("source") or "") for x in bank]
    dialogue_bad = [
        {"id": x.get("id"), "turns": len(x.get("dialogue", [])) if isinstance(x.get("dialogue"), list) else None}
        for x in bank
        if not isinstance(x.get("dialogue"), list) or not 3 <= len(x.get("dialogue")) <= 6
    ]

    spotify_index = build_spotify_index()
    spotify_overrides = load_spotify_overrides()
    spotify_missing = []
    spotify_stored_mismatches = []
    for item in bank:
        resolved = resolve_spotify(item, spotify_index, spotify_overrides)
        if not resolved:
            spotify_missing.append({"id": item.get("id"), "episode_title": item.get("episode_title")})
            continue
        stored = str(item.get("spotify_url") or "").strip()
        if stored and stored.startswith("https://open.spotify.com/episode/") and stored != resolved:
            spotify_stored_mismatches.append({"id": item.get("id"), "stored": stored, "resolved": resolved})

    listen_mismatches: list[dict] = []
    listen_dialogue_mismatches: list[dict] = []
    listen_fetch_errors: list[dict] = []
    listen_verified = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(verify_listen, item) for item in bank]
        for future in as_completed(futures):
            status, result = future.result()
            if status == "ok":
                listen_verified += 1
            elif status == "mismatch":
                listen_mismatches.append(result)
            elif status == "dialogue_mismatch":
                listen_dialogue_mismatches.append(result)
            else:
                listen_fetch_errors.append(result)

    report = {
        "summary": {
            "canonical_bank_items": len(bank),
            "unique_ids": len(set(ids)),
            "unique_episode_sources": len(set(sources)),
            "extra_clips": sum(1 for x in bank if x.get("parent_id")),
            "dialogue_shape_errors": len(dialogue_bad),
            "spotify_unresolved": len(spotify_missing),
            "spotify_stored_mismatches": len(spotify_stored_mismatches),
            "listen_titles_verified": listen_verified,
            "listen_source_mismatches": len(listen_mismatches),
            "listen_extra_dialogue_mismatches": len(listen_dialogue_mismatches),
            "listen_fetch_errors": len(listen_fetch_errors),
        },
        "dialogue_shape_errors": dialogue_bad,
        "spotify_unresolved": spotify_missing,
        "spotify_stored_mismatches": spotify_stored_mismatches,
        "listen_mismatches": sorted(listen_mismatches, key=lambda x: str(x.get("id"))),
        "listen_extra_dialogue_mismatches": sorted(listen_dialogue_mismatches, key=lambda x: str(x.get("id"))),
        "listen_fetch_errors": sorted(listen_fetch_errors, key=lambda x: str(x.get("id"))),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))

    hard_errors = (
        len(set(ids)) != len(bank)
        or len(set(sources)) != BASE_EPISODE_COVERAGE
        or dialogue_bad
        or spotify_missing
        or spotify_stored_mismatches
        or listen_mismatches
        or listen_dialogue_mismatches
        or listen_fetch_errors
    )
    if hard_errors:
        raise RuntimeError("canonical funny clip quality audit failed; inspect funny_clip_quality_report.json")


if __name__ == "__main__":
    main()
