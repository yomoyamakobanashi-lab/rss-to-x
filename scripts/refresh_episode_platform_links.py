#!/usr/bin/env python3
"""Refresh the verified Spotify / Apple Podcasts / YouTube episode catalogue."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

from scripts.episode_links import (
    CATALOG_PATH,
    ROOT,
    SPOTIFY_RE,
    normalize_title,
    validate_catalog,
)

SPOTIFY_PATH = ROOT / "data" / "spotify_episodes.json"
APPLE_COLLECTION_ID = "1810778208"
YOUTUBE_PLAYLIST_ID = "PLYmlpbAXfSqRgb4mdFLmV3ol1PsL0NUzo"
USER_AGENT = "Mozilla/5.0 (compatible; ReelPalLinkAudit/1.0)"


def _partition_spotify(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate publishable episodes from new episodes awaiting a Spotify URL."""
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Spotify episode index is unavailable")

    resolved: list[dict] = []
    pending: list[dict] = []
    seen_guids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Spotify episode index contains an invalid row")
        guid = str(row.get("guid") or "").strip()
        if not guid or guid in seen_guids:
            raise RuntimeError("Spotify episode index contains a missing or duplicate GUID")
        seen_guids.add(guid)

        url = str(row.get("spotifyUrl") or "").split("?", 1)[0].strip()
        if not url:
            pending.append(row)
            continue
        if not SPOTIFY_RE.fullmatch(url):
            raise RuntimeError(f"Spotify episode index contains an invalid URL: {url}")
        resolved.append(row)

    if not resolved:
        raise RuntimeError("Spotify episode index has no resolved episode URLs")
    return resolved, pending


def validate_index_catalog_alignment(spotify: list[dict], catalog: list[dict]) -> dict[str, int]:
    """Require every resolved Spotify row, while allowing genuinely pending new rows.

    A new RSS item can appear before Spotify exposes its episode URL.  That item
    must not invalidate the already verified catalogue or stop unrelated daily
    posts.  As soon as a URL becomes available it moves into ``resolved`` and a
    missing catalogue row becomes a hard failure again.
    """
    resolved, pending = _partition_spotify(spotify)
    expected = {
        str(row["guid"]): str(row["spotifyUrl"]).split("?", 1)[0]
        for row in resolved
    }
    actual = {
        str(row.get("guid") or "").strip(): str(row.get("spotify_url") or "").strip()
        for row in catalog
        if isinstance(row, dict)
    }
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        mismatched = sorted(
            guid for guid in set(expected) & set(actual) if expected[guid] != actual[guid]
        )
        raise RuntimeError(
            "platform catalogue differs from resolved Spotify episodes: "
            f"missing={len(missing)}, stale={len(stale)}, mismatched={len(mismatched)}, "
            f"pending={len(pending)}"
        )
    return {
        "index_episodes": len(spotify),
        "resolved_episodes": len(resolved),
        "pending_episodes": len(pending),
        "catalog_episodes": len(catalog),
    }


def _fetch(url: str, *, data: bytes | None = None, content_type: str = "") -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_apple() -> dict[str, dict]:
    url = (
        "https://itunes.apple.com/lookup?"
        f"id={APPLE_COLLECTION_ID}&entity=podcastEpisode&limit=200&country=jp"
    )
    payload = json.loads(_fetch(url))
    episodes = [
        row
        for row in payload.get("results", [])
        if row.get("wrapperType") == "podcastEpisode" or row.get("kind") == "podcast-episode"
    ]
    return {
        str(row.get("episodeGuid") or "").strip(): {
            "id": str(row.get("trackId") or "").strip(),
            "title": str(row.get("trackName") or "").strip(),
        }
        for row in episodes
        if str(row.get("episodeGuid") or "").strip()
        and str(row.get("trackId") or "").isdigit()
    }


def _walk_youtube(value, videos: list[dict], continuations: list[str]) -> None:
    if isinstance(value, dict):
        lockup = value.get("lockupViewModel")
        if isinstance(lockup, dict):
            title = (
                lockup.get("metadata", {})
                .get("lockupMetadataViewModel", {})
                .get("title", {})
                .get("content")
            )
            video_id = lockup.get("contentId")
            if (
                lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO"
                and isinstance(video_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                and isinstance(title, str)
            ):
                videos.append({"id": video_id, "title": title})
        legacy = value.get("playlistVideoRenderer")
        if isinstance(legacy, dict):
            runs = legacy.get("title", {}).get("runs", [])
            title = "".join(str(run.get("text") or "") for run in runs)
            video_id = str(legacy.get("videoId") or "")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and title:
                videos.append({"id": video_id, "title": title})
        token = value.get("continuationCommand", {}).get("token")
        if isinstance(token, str) and token:
            continuations.append(token)
        for child in value.values():
            _walk_youtube(child, videos, continuations)
    elif isinstance(value, list):
        for child in value:
            _walk_youtube(child, videos, continuations)


def fetch_youtube() -> list[dict]:
    html = _fetch(
        f"https://www.youtube.com/playlist?list={YOUTUBE_PLAYLIST_ID}"
    ).decode("utf-8", errors="replace")
    data_match = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
    key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    version_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    if not data_match or not key_match or not version_match:
        raise RuntimeError("YouTube playlist metadata format was not recognized")

    videos: list[dict] = []
    continuations: list[str] = []
    _walk_youtube(json.loads(data_match.group(1)), videos, continuations)
    for token in dict.fromkeys(continuations):
        body = json.dumps(
            {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": version_match.group(1),
                    }
                },
                "continuation": token,
            }
        ).encode("utf-8")
        payload = json.loads(
            _fetch(
                "https://www.youtube.com/youtubei/v1/browse?key=" + key_match.group(1),
                data=body,
                content_type="application/json",
            )
        )
        more: list[str] = []
        _walk_youtube(payload, videos, more)

    return list({row["id"]: row for row in videos}.values())


def _source_aliases() -> list[tuple[str, str]]:
    paths = [
        ROOT / "data" / "trend_episode_topics.json",
        ROOT / "data" / "social_pack_queue.json",
        ROOT / "data" / "audio_clip_queue.json",
        ROOT / "data" / "funny_clip_posts.json",
        ROOT / "data" / "funny_clip_posts_all_episodes.json",
        ROOT / "data" / "funny_clip_legacy_canonical.json",
        ROOT / "data" / "generated_chapters" / "latest.json",
        *sorted((ROOT / "data").glob("funny_clip_extras*.json")),
    ]
    aliases: list[tuple[str, str]] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(
                row.get("source_url")
                or row.get("listen_url")
                or row.get("listen_episode_url")
                or ""
            ).split("?", 1)[0].rstrip("/")
            title = str(
                row.get("episode_title") or row.get("title") or row.get("source") or ""
            ).strip()
            if url.startswith("https://listen.style/p/reelpal/") and title:
                aliases.append((url, title))
    return list(dict.fromkeys(aliases))


def _match_title(
    title: str,
    rows: list[dict],
    *,
    youtube: bool = False,
    min_prefix: int = 18,
) -> dict | None:
    wanted = normalize_title(title).rstrip("…")
    candidates: list[dict] = []
    for row in rows:
        candidate = normalize_title(row.get("title")).rstrip("…")
        if wanted == candidate:
            candidates.append(row)
        elif youtube and min(len(wanted), len(candidate)) >= min_prefix and (
            wanted.startswith(candidate) or candidate.startswith(wanted)
        ):
            candidates.append(row)
    unique = {str(row.get("id") or row.get("guid")): row for row in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def refresh() -> dict[str, int]:
    spotify = json.loads(SPOTIFY_PATH.read_text(encoding="utf-8"))
    resolved_spotify, pending_spotify = _partition_spotify(spotify)

    try:
        old_rows = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        old_rows = []
    old_by_guid = {
        str(row.get("guid")): row for row in old_rows if isinstance(row, dict) and row.get("guid")
    }

    try:
        apple = fetch_apple()
    except Exception as exc:
        print(f"[WARN] Apple Podcasts refresh failed; preserving verified URLs: {exc}")
        apple = {}
    try:
        youtube = fetch_youtube()
    except Exception as exc:
        print(f"[WARN] YouTube refresh failed; preserving verified URLs: {exc}")
        youtube = []
    aliases = _source_aliases()
    aliases_by_guid: dict[str, set[str]] = {}
    for url, alias_title in aliases:
        matched_episode = _match_title(alias_title, resolved_spotify, youtube=True, min_prefix=12)
        if matched_episode:
            aliases_by_guid.setdefault(str(matched_episode.get("guid") or ""), set()).add(url)
    rows: list[dict] = []
    for episode in resolved_spotify:
        guid = str(episode.get("guid") or "").strip()
        title = str(episode.get("title") or "").strip()
        old = old_by_guid.get(guid, {})
        apple_row = apple.get(guid)
        youtube_row = _match_title(title, youtube, youtube=True)
        listen_urls = sorted(aliases_by_guid.get(guid, set()))
        if not listen_urls:
            listen_urls = [str(url) for url in old.get("listen_urls", [])]

        apple_id = apple_row.get("id") if apple_row else ""
        apple_url = (
            f"https://podcasts.apple.com/jp/podcast/id{APPLE_COLLECTION_ID}?i={apple_id}"
            if apple_id
            else str(old.get("apple_url") or "")
        )
        youtube_url = (
            f"https://youtu.be/{youtube_row['id']}"
            if youtube_row
            else str(old.get("youtube_url") or "")
        )
        rows.append(
            {
                "guid": guid,
                "title": title,
                "spotify_url": str(episode["spotifyUrl"]).split("?", 1)[0],
                "apple_url": apple_url,
                "youtube_url": youtube_url,
                "listen_urls": sorted(set(listen_urls)),
            }
        )

    CATALOG_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    from scripts import episode_links

    episode_links.load_catalog.cache_clear()
    counts = validate_catalog()
    if (
        counts["episodes"] != len(resolved_spotify)
        or counts["spotify"] != len(resolved_spotify)
    ):
        raise RuntimeError(f"incomplete platform catalogue: {counts}")
    if pending_spotify:
        print(
            "[WARN] New episode(s) awaiting exact Spotify URLs; "
            f"kept out of listener-facing links without blocking existing posts: {len(pending_spotify)}"
        )
    print(
        "[OK] platform links: "
        f"Spotify {counts['spotify']}/{counts['episodes']}, "
        f"Apple {counts['apple']}/{counts['episodes']}, "
        f"YouTube {counts['youtube']}/{counts['episodes']}"
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate checked-in data without network")
    args = parser.parse_args()
    if args.check:
        counts = validate_catalog()
        if counts["spotify"] != counts["episodes"]:
            raise RuntimeError(f"incomplete required platform coverage: {counts}")
        spotify = json.loads(SPOTIFY_PATH.read_text(encoding="utf-8"))
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        alignment = validate_index_catalog_alignment(spotify, catalog)
        if alignment["pending_episodes"]:
            print(
                "[WARN] Exact Spotify URL pending for "
                f"{alignment['pending_episodes']} new episode(s); existing verified links remain usable"
            )
        print(json.dumps({**counts, **alignment}, ensure_ascii=False))
        return
    refresh()


if __name__ == "__main__":
    main()
