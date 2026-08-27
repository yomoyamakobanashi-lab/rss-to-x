#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_thread

BANK_PATH = ROOT / "data" / "funny_clip_posts.json"
STATE_PATH = ROOT / "state_funny_clip.json"
RECENT_SOURCE_WINDOW = 7
RECENT_TOPIC_WINDOW = 14

LEAD_VARIANTS = [
    lambda text: f"{text}\n\nリルパル、こういう脱線もしています。",
    lambda text: f"映画の話をしていたはずなんですが。\n\n{text}",
    lambda text: f"{text}\n\nこういう話も普通にしています。",
    lambda text: f"インターミッションから一場面。\n\n{text}",
]


def load_bank() -> list[dict]:
    data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
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
        # Root text receives a short framing line later; keep a healthy margin under X's limit.
        if len(text) > 220:
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

    # Prefer a full 7-day episode cooldown. If the bank composition makes that impossible,
    # relax to 4 days, then only preserve topic uniqueness. Never recycle a used clip.
    for source_window in (RECENT_SOURCE_WINDOW, 4, 0):
        recent_sources = (
            set(state["recent_sources"][-source_window:]) if source_window else set()
        )
        candidates = [
            item
            for item in unused
            if item["source"] not in recent_sources and item["topic"] not in recent_topics
        ]
        if candidates:
            return candidates[0]

    # Topics are also unique in the initial bank, but keep a final safety fallback for future edits.
    return unused[0]


def render_root(item: dict, used_count: int) -> str:
    base = item["text"].strip()
    root = LEAD_VARIANTS[used_count % len(LEAD_VARIANTS)](base)
    if len(root) > 280:
        raise RuntimeError(f"rendered funny clip exceeds 280 chars: {item['id']} ({len(root)})")
    return root


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


def main() -> None:
    bank = load_bank()
    state = load_state()
    item = pick_clip(bank, state)
    if item is None:
        print(
            f"[INFO] funny clip bank exhausted: used={len(state['used_ids'])}, total={len(bank)}. "
            "No post sent; replenish with LISTEN-grounded clips."
        )
        return

    root = render_root(item, len(state["used_ids"]))
    reply = (
        "この脱線はLISTENで👇\n"
        f"{item['source_url']}\n\n"
        "映画の話から、だいたいこうなります。"
    )
    if len(reply) > 280:
        raise RuntimeError(f"funny clip reply exceeds 280 chars: {item['id']}")

    post_id = post_thread([root, reply])
    save_state(state, item)

    remaining = len(bank) - len(state["used_ids"]) - 1
    print(
        f"[OK] Buffer accepted funny clip thread: {post_id}; id={item['id']}; "
        f"source={item['source']}; topic={item['topic']}; remaining={remaining}"
    )


if __name__ == "__main__":
    main()
