#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import json
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts import funny_clip_buffer as base

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "data" / "spotify_episode_overrides.json"
SHOW_ID = "4o8l9DJWMuwUht2pvkEytS"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/132 Safari/537.36"


def _json_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    req = Request(url, headers=request_headers)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_post(url: str, data: dict[str, str], headers: dict[str, str] | None = None, timeout: int = 30) -> dict:
    body = urlencode(data).encode("utf-8")
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if headers:
        request_headers.update(headers)
    req = Request(url, data=body, headers=request_headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _spotify_token() -> str:
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        payload = _json_post(
            "https://accounts.spotify.com/api/token",
            {"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
        )
        token = str(payload.get("access_token") or "").strip()
        if token:
            return token

    # Spotify's web player exposes a short-lived anonymous token. This keeps the
    # resolver useful without additional secrets; client credentials are preferred
    # automatically whenever they are available.
    payload = _json_get(
        "https://open.spotify.com/get_access_token?reason=transport&productType=web_player",
        headers={"Referer": "https://open.spotify.com/"},
    )
    token = str(payload.get("accessToken") or "").strip()
    if not token:
        raise RuntimeError("Could not obtain a Spotify access token")
    return token


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    value = value.replace("replay", "")
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]/\\]", "", value)
    return value


def _similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        return 0.86 + 0.14 * (shorter / longer)
    return SequenceMatcher(None, na, nb).ratio()


def _fetch_all_show_episodes(token: str) -> list[dict]:
    episodes: list[dict] = []
    offset = 0
    while True:
        params = urlencode({"market": "JP", "limit": 50, "offset": offset})
        payload = _json_get(
            f"https://api.spotify.com/v1/shows/{SHOW_ID}/episodes?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise RuntimeError("Spotify show episode response is invalid")
        episodes.extend(x for x in items if isinstance(x, dict))
        if not payload.get("next") or len(items) == 0:
            break
        offset += len(items)
        time.sleep(0.15)
    if not episodes:
        raise RuntimeError("Spotify returned no episodes for Reel Friends in TOKYO")
    return episodes


def _load_overrides() -> dict[str, str]:
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if str(v).strip().startswith("https://open.spotify.com/episode/")
    } if isinstance(data, dict) else {}


def _best_match(title: str, episodes: list[dict]) -> tuple[dict | None, float, float]:
    scored: list[tuple[float, dict]] = []
    for episode in episodes:
        candidate = str(episode.get("name") or "")
        score = _similarity(title, candidate)
        scored.append((score, episode))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return None, 0.0, 0.0
    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    return best, best_score, second


def resolve_all() -> dict[str, str]:
    bank = base.load_bank()
    source_titles: dict[str, str] = {}
    for item in bank:
        source = str(item["source"]).strip()
        title = str(item.get("episode_title") or "").strip()
        if source in source_titles and source_titles[source] != title:
            raise RuntimeError(f"Conflicting episode titles for source {source}")
        source_titles[source] = title

    overrides = _load_overrides()
    missing = {source: title for source, title in source_titles.items() if source not in overrides}
    if not missing:
        print(f"[OK] all {len(source_titles)} funny-clip source episodes already have Spotify direct links")
        return overrides

    token = _spotify_token()
    episodes = _fetch_all_show_episodes(token)
    print(f"[INFO] fetched {len(episodes)} Spotify episodes; resolving {len(missing)} missing source links")

    unresolved: list[str] = []
    diagnostics: list[str] = []
    for source, title in sorted(missing.items()):
        best, score, second = _best_match(title, episodes)
        if best is None:
            unresolved.append(source)
            diagnostics.append(f"{source}: no candidate")
            continue

        url = str((best.get("external_urls") or {}).get("spotify") or "").strip()
        candidate_title = str(best.get("name") or "").strip()
        margin = score - second
        # Same-show enumeration removes publisher ambiguity. Require a strong
        # textual match, or a moderately strong match with a clear lead over the
        # runner-up, so a generic intermission title can never silently map wrong.
        accepted = score >= 0.72 or (score >= 0.60 and margin >= 0.08)
        if not accepted or not url.startswith("https://open.spotify.com/episode/"):
            unresolved.append(source)
            diagnostics.append(
                f"{source}: score={score:.3f} margin={margin:.3f} bank={title!r} spotify={candidate_title!r}"
            )
            continue

        overrides[source] = url
        print(f"[OK] {source} -> {url} ({score:.3f}) {candidate_title}")

    OVERRIDES_PATH.write_text(
        json.dumps(dict(sorted(overrides.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if unresolved:
        details = "\n".join(diagnostics)
        raise RuntimeError(
            f"Spotify direct-link resolution incomplete: {len(unresolved)} source(s) unresolved.\n{details}"
        )

    print(f"[OK] resolved Spotify direct links for all {len(source_titles)} funny-clip source episodes")
    return overrides


def main() -> None:
    resolve_all()


if __name__ == "__main__":
    main()
