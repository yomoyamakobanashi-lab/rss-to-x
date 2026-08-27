#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_text

POSTS_PATHS = [
    ROOT / "data" / "discussion_posts.json",
    ROOT / "data" / "discussion_posts_extra.json",
]
STATE_PATH = ROOT / "state_discussion.json"


def load_posts() -> list[dict]:
    posts: list[dict] = []
    for path in POSTS_PATHS:
        if not path.exists():
            continue
        chunk = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(chunk, list):
            raise RuntimeError(f"{path.name} is invalid")
        posts.extend(chunk)

    if not posts:
        raise RuntimeError("discussion post bank is empty")

    for post in posts:
        text = (post.get("text") or "").strip()
        if not text:
            raise RuntimeError("discussion post contains empty text")
        if len(text) > 280:
            raise RuntimeError(f"discussion post exceeds 280 chars: {len(text)}")
    return posts


def load_index() -> int:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return max(0, int(state.get("next_index", 0)))
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def main() -> None:
    posts = load_posts()
    index = load_index()

    # Do not silently recycle old posts. Replenish the bank and continue from this index.
    if index >= len(posts):
        print(
            f"[INFO] discussion bank exhausted: next_index={index}, total={len(posts)}. "
            "No post sent; add new LISTEN-grounded posts."
        )
        return

    post = posts[index]
    text = post["text"].strip()
    post_id = post_text(text)

    next_index = index + 1
    STATE_PATH.write_text(
        json.dumps({"next_index": next_index}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    source = post.get("source", "")
    remaining = len(posts) - next_index
    print(
        f"[OK] Buffer accepted discussion post: {post_id}; "
        f"source={source}; next_index={next_index}; remaining={remaining}"
    )


if __name__ == "__main__":
    main()
