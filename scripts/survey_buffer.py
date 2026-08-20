#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_text

INDEX_PATH = Path("index.txt")
TWEETS_PATH = Path("tweets.txt")


def main() -> None:
    lines = [line.strip() for line in TWEETS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("tweets.txt is empty")

    try:
        index = int(INDEX_PATH.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, ValueError):
        index = 0

    index %= len(lines)
    text = lines[index]
    post_id = post_text(text)

    next_index = (index + 1) % len(lines)
    INDEX_PATH.write_text(str(next_index) + "\n", encoding="utf-8")
    print(f"[OK] Buffer accepted survey post: {post_id}; next_index={next_index}")


if __name__ == "__main__":
    main()
