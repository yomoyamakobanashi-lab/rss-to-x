#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_text

POSTS_PATH = ROOT / "data" / "discussion_posts.json"
STATE_PATH = ROOT / "state_discussion.json"


def load_posts() -> list[dict]:
    posts = json.loads(POSTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(posts, list) or not posts:
        raise RuntimeError("discussion_posts.json is empty or invalid")
    for post in posts:
        text = (post.get("text") or "").strip()
        if not text:
            raise RuntimeError("discussion post contains empty text")
        if len(text) > 280:
            raise RuntimeError(f"discussion post exceeds 280 chars: {len(text)}")
    return posts


def load_index(total: int) -> int:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return int(state.get("next_index", 0)) % total
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def main() -> None:
    posts = load_posts()
    index = load_index(len(posts))
    post = posts[index]
    text = post["text"].strip()

    post_id = post_text(text)

    next_index = (index + 1) % len(posts)
    STATE_PATH.write_text(
        json.dumps({"next_index": next_index}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    source = post.get("source", "")
    print(f"[OK] Buffer accepted discussion post: {post_id}; source={source}; next_index={next_index}")


if __name__ == "__main__":
    main()
