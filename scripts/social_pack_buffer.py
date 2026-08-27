#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import BufferError, post_text, post_thread
from note_poster import x_length

QUEUE_PATH = ROOT / "data" / "social_pack_queue.json"
STATE_PATH = ROOT / "state_social_pack.json"
JST = ZoneInfo("Asia/Tokyo")
ROOT_LIMIT = 280

ALLOWED_KINDS = {
    "three_hooks",
    "host_split",
    "winner_remix",
    "episode_hook",
}


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(timezone.utc)


def load_queue() -> list[dict]:
    data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("social pack queue must be a JSON array")

    seen: set[str] = set()
    for item in data:
        item_id = str(item.get("id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        not_before = str(item.get("not_before") or "").strip()
        if not item_id or kind not in ALLOWED_KINDS or not text or not not_before:
            raise RuntimeError(f"invalid social pack entry: {item}")
        if item_id in seen:
            raise RuntimeError(f"duplicate social pack id: {item_id}")
        seen.add(item_id)
        if x_length(text) > ROOT_LIMIT:
            raise RuntimeError(f"social pack root exceeds X limit: {item_id}")
        if "http://" in text or "https://" in text:
            raise RuntimeError(f"social pack root must remain link-free: {item_id}")
        if source_url and not source_url.startswith("https://listen.style/p/reelpal/"):
            raise RuntimeError(f"source_url must be ReelPal LISTEN URL: {item_id}")
        if parse_dt(not_before) is None:
            raise RuntimeError(f"invalid not_before: {item_id}")
    return data


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        data = {}
    return {
        "posted_ids": [str(x) for x in data.get("posted_ids", []) if str(x).strip()],
        "last_post_date_jst": data.get("last_post_date_jst"),
    }


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def build_reply(item: dict) -> str | None:
    source_url = str(item.get("source_url") or "").strip()
    if not source_url:
        return None
    label = {
        "three_hooks": "この回はLISTENで👇",
        "host_split": "二人の着地点は本編で👇",
        "episode_hook": "この話の続きはLISTENで👇",
        "winner_remix": "元になった回はこちら👇",
    }.get(item["kind"], "本編はこちら👇")
    reply = f"{label}\n{source_url}"
    if x_length(reply) > ROOT_LIMIT:
        raise RuntimeError(f"social pack reply exceeds X limit: {item['id']}")
    return reply


def pick_due(queue: list[dict], state: dict, now: datetime) -> dict | None:
    posted = set(state["posted_ids"])
    due = []
    for item in queue:
        if item["id"] in posted:
            continue
        due_at = parse_dt(item["not_before"])
        if due_at and due_at <= now:
            due.append((due_at, item))
    if not due:
        return None
    due.sort(key=lambda x: (x[0], str(x[1]["id"])))
    return due[0][1]


def main() -> None:
    queue = load_queue()
    state = load_state()
    now = datetime.now(timezone.utc)
    today_jst = now.astimezone(JST).date().isoformat()

    # This layer is supplemental; cap it at one root post per local calendar day.
    if state.get("last_post_date_jst") == today_jst:
        print("[INFO] social pack already posted today; skip")
        return

    item = pick_due(queue, state, now)
    if item is None:
        print("[INFO] no due social pack item")
        return

    root = str(item["text"]).strip()
    reply = build_reply(item)
    try:
        if reply:
            post_id = post_thread([root, reply])
        else:
            post_id = post_text(root)
    except BufferError as exc:
        print(f"[ERROR] Buffer social pack post failed: {exc}", file=sys.stderr)
        raise

    state["posted_ids"] = state["posted_ids"] + [item["id"]]
    state["last_post_date_jst"] = today_jst
    save_state(state)
    print(
        f"[OK] Buffer accepted social pack: {post_id}; "
        f"id={item['id']}; kind={item['kind']}"
    )


if __name__ == "__main__":
    main()
