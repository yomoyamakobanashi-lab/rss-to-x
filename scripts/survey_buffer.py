#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_text, post_thread

INDEX_PATH = Path("index.txt")
TWEETS_PATH = Path("tweets.txt")
URL_RE = re.compile(r"https?://\S+")


def split_prompt_and_url(text: str) -> tuple[str, str]:
    match = URL_RE.search(text)
    if not match:
        return text.strip(), ""

    url = match.group(0).rstrip(".,。！？!?)）]}")
    root = (text[: match.start()] + text[match.end() :]).strip()
    root = root.replace("#リルパル", "").replace("#ReelPal", "")
    root = " ".join(root.split()).strip()
    return root, url


def main() -> None:
    lines = [line.strip() for line in TWEETS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("tweets.txt is empty")

    try:
        index = int(INDEX_PATH.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, ValueError):
        index = 0

    index %= len(lines)
    root, url = split_prompt_and_url(lines[index])
    if not root:
        raise RuntimeError("survey root text is empty")

    if url:
        reply = f"リクエスト／感想フォームはこちら👇\n{url}"
        post_id = post_thread([root, reply])
    else:
        post_id = post_text(root)

    next_index = (index + 1) % len(lines)
    INDEX_PATH.write_text(str(next_index) + "\n", encoding="utf-8")
    print(f"[OK] Buffer accepted survey post: {post_id}; next_index={next_index}")


if __name__ == "__main__":
    main()
