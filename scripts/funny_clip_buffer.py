#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_thread

BANK_PATHS = [
    ROOT / "data" / "funny_clip_posts.json",
    ROOT / "data" / "funny_clip_posts_archive.json",
    ROOT / "data" / "funny_clip_posts_archive_2.json",
]
STATE_PATH = ROOT / "state_funny_clip.json"
RECENT_SOURCE_WINDOW = 10
RECENT_TOPIC_WINDOW = 18


def load_bank() -> list[dict]:
    data: list[dict] = []
    for path in BANK_PATHS:
        if not path.exists():
            continue
        chunk = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(chunk, list):
            raise RuntimeError(f"funny clip bank is invalid: {path.name}")
        data.extend(chunk)

    if not data:
        raise RuntimeError("funny clip bank is empty or invalid")

    seen_ids: set[str] = set()
    for item in data:
        clip_id = str(item.get("id") or "").strip()
        source = str(item.get("source") or "").strip()
        topic = str(item.get("topic") or "").strip()
        text = str(item.get("text") or "").strip()
        url = str(item.get("source_url") or "").strip()
        if not all([clip_id, source, topic, text, url]):
            raise RuntimeError(f"funny clip entry is incomplete: {item}")
        if clip_id in seen_ids:
            raise RuntimeError(f"duplicate funny clip id: {clip_id}")
        seen_ids.add(clip_id)
        if not url.startswith("https://listen.style/p/reelpal/"):
            raise RuntimeError(f"funny clip source is not a ReelPal LISTEN URL: {url}")
        if len(text) > 250:
            raise RuntimeError(f"funny clip base text is too long ({len(text)}): {clip_id}")
    return data


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        state = {}

    return {
        "used_ids": [str(x) for x in state.get("used_ids", []) if str(x).strip()],
        "recent_sources": [str(x) for x in state.get("recent_sources", []) if str(x).strip()],
        "recent_topics": [str(x) for x in state.get("recent_topics", []) if str(x).strip()],
    }


def pick_clip(bank: list[dict], state: dict) -> dict | None:
    used = set(state["used_ids"])
    unused = [item for item in bank if item["id"] not in used]
    if not unused:
        return None

    recent_topics = set(state["recent_topics"][-RECENT_TOPIC_WINDOW:])
    for source_window in (RECENT_SOURCE_WINDOW, 6, 3, 0):
        recent_sources = set(state["recent_sources"][-source_window:]) if source_window else set()
        candidates = [
            item
            for item in unused
            if item["source"] not in recent_sources and item["topic"] not in recent_topics
        ]
        if candidates:
            return candidates[0]
    return unused[0]


def render_root(item: dict) -> str:
    root = item["text"].strip()
    if len(root) > 250:
        raise RuntimeError(f"rendered funny clip is too long: {item['id']} ({len(root)})")
    return root


def render_reply(item: dict) -> str:
    reply = f"元の脱線はLISTENで👇\n{item['source_url']}"
    if len(reply) > 260:
        raise RuntimeError(f"funny clip reply exceeds safe length: {item['id']}")
    return reply


def save_state(state: dict, item: dict) -> None:
    used_ids = state["used_ids"] + [item["id"]]
    recent_sources = (state["recent_sources"] + [item["source"]])[-RECENT_SOURCE_WINDOW:]
    recent_topics = (state["recent_topics"] + [item["topic"]])[-RECENT_TOPIC_WINDOW:]
    STATE_PATH.write_text(
        json.dumps(
            {
                "used_ids": used_ids,
                "recent_sources": recent_sources,
                "recent_topics": recent_topics,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def validate_full_bank(bank: list[dict]) -> None:
    for item in bank:
        render_root(item)
        render_reply(item)
    print(f"[OK] validated {len(bank)} LISTEN-grounded funny clips")


def main() -> None:
    bank = load_bank()
    state = load_state()

    if os.getenv("FUNNY_CLIP_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
        validate_full_bank(bank)
        item = pick_clip(bank, state)
        if item:
            print(f"[DRY RUN] next id={item['id']} source={item['source']} topic={item['topic']}")
            print(render_root(item))
            print("--- reply ---")
            print(render_reply(item))
        return

    item = pick_clip(bank, state)
    if item is None:
        print(
            f"[INFO] funny clip bank exhausted: used={len(state['used_ids'])}, total={len(bank)}. "
            "No post sent; replenish with LISTEN-grounded clips from the archive."
        )
        return

    root = render_root(item)
    reply = render_reply(item)
    post_id = post_thread([root, reply])
    save_state(state, item)

    remaining = len(bank) - len(state["used_ids"]) - 1
    print(
        f"[OK] Buffer accepted funny clip thread: {post_id}; id={item['id']}; "
        f"source={item['source']}; topic={item['topic']}; remaining={remaining}"
    )


if __name__ == "__main__":
    main()
