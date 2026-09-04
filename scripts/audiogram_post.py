#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import BufferError, post_video_thread
from note_poster import x_length
from scripts.episode_links import render_episode_reply

META_PATH = ROOT / "audiogram_meta.json"
STATE_PATH = ROOT / "state_audio_clip.json"
ROOT_LIMIT = 280


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        data = {}
    return {"posted_ids": [str(x) for x in data.get("posted_ids", []) if str(x).strip()]}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def wait_for_public_video(url: str) -> None:
    last = None
    for attempt in range(8):
        try:
            response = requests.get(url, headers={"Range": "bytes=0-1023"}, timeout=20)
            last = response.status_code
            if response.status_code in (200, 206) and response.content:
                return
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(min(2 + attempt * 2, 12))
    raise RuntimeError(f"public audiogram URL not ready: {url}; last={last}")


def main() -> None:
    if not META_PATH.exists():
        print("[INFO] no audiogram_meta.json; skip")
        return

    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    clip_id = str(meta.get("id") or "").strip()
    caption = str(meta.get("caption") or "").strip()
    listen_url = str(meta.get("listen_url") or "").strip()
    public_url = (os.getenv("AUDIOGRAM_PUBLIC_URL") or "").strip()

    if not clip_id or not caption or not listen_url or not public_url:
        raise RuntimeError("audiogram post metadata is incomplete")
    if x_length(caption) > ROOT_LIMIT:
        raise RuntimeError(f"audiogram caption exceeds X limit: {clip_id}")
    if "http://" in caption or "https://" in caption:
        raise RuntimeError(f"audiogram root must remain link-free: {clip_id}")
    if not listen_url.startswith("https://listen.style/p/reelpal/"):
        raise RuntimeError(f"invalid LISTEN URL: {clip_id}")
    if not public_url.startswith("https://raw.githubusercontent.com/"):
        raise RuntimeError("audiogram public URL must be a raw.githubusercontent.com HTTPS URL")

    state = load_state()
    if clip_id in set(state["posted_ids"]):
        print(f"[INFO] audiogram already posted: {clip_id}")
        return

    reply = render_episode_reply(
        title=meta.get("rss_title") or meta.get("episode_title"),
        listen_url=listen_url,
        intro="🎧 このくだりの本編を聴く",
    )
    if x_length(reply) > ROOT_LIMIT:
        raise RuntimeError(f"audiogram reply exceeds X limit: {clip_id}")

    wait_for_public_video(public_url)
    try:
        post_id = post_video_thread([caption, reply], public_url)
    except BufferError as exc:
        print(f"[ERROR] Buffer audiogram post failed: {exc}", file=sys.stderr)
        raise

    state["posted_ids"] = state["posted_ids"] + [clip_id]
    save_state(state)
    print(f"[OK] Buffer accepted audiogram: {post_id}; id={clip_id}; media={public_url}")


if __name__ == "__main__":
    main()
