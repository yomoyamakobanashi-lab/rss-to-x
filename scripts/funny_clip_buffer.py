#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
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
    ROOT / "data" / "funny_clip_posts_archive_3.json",
]
STATE_PATH = ROOT / "state_funny_clip.json"
RECENT_SOURCE_WINDOW = 10
RECENT_TOPIC_WINDOW = 18
REELPAL_TAG = "#リルパル"


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


def _clean_fragment(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" 。、\n\t")
    return value


def _clip_fragment(value: str, limit: int = 72) -> str:
    value = _clean_fragment(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip("、。 ") + "…"


def _dialogue_lines(text: str, topic: str) -> tuple[str, str]:
    """Turn transcript-grounded copy into short, conversational social copy.

    Existing Japanese quote spans are preferred. If an entry is descriptive rather
    than already quoted, its own first clauses are tightened into dialogue-like
    paraphrases; no new factual detail or speaker attribution is introduced.
    """
    quotes = [_clip_fragment(q) for q in re.findall(r"「([^」]+)」", text) if _clean_fragment(q)]
    if len(quotes) >= 2:
        return quotes[0], quotes[1]

    stripped = re.sub(r"「[^」]+」", "", text)
    clauses = [
        _clip_fragment(part)
        for part in re.split(r"[。！？!?]+", stripped)
        if _clean_fragment(part)
    ]

    if quotes:
        first = quotes[0]
        second = clauses[0] if clauses else f"{topic}、どうなってんだよ"
        return first, second

    if len(clauses) >= 2:
        return clauses[0], clauses[1]
    if clauses:
        return clauses[0], f"いや、{topic}どうなってんだよ"
    return topic, f"いや、{topic}どうなってんだよ"


def _punchline(item: dict) -> str:
    topic = _clean_fragment(item["topic"])
    clip_id = str(item.get("id") or "")

    # A few recurring topics benefit from a more specific final beat than the
    # generic fallback. These remain grounded in the stored clip summary.
    specific = {
        "orangutan-fringe-mystery": "オランウータンのフランジ、何でできてるかわからな過ぎて面白すぎる。",
        "experiment-sea-creatures": "イセエビ、海にいるという一点だけでだいぶ得してる。",
        "wicked-giron-crawling": "言われた瞬間からもうハイハイしてる人にしか見えなくなった。",
        "orangutan-old-man-face": "オランウータン、老いると急に人生2周目みたいな顔になる。",
        "terminator-dyson": "ダイソン、名前だけで掃除機の方が強すぎる。",
        "terminator3-dead-still-working": "乳酸菌、死してなお働かされるの業が深すぎる。",
        "whiplash-oizumi": "映画ポッドキャストなのに毎回ほぼ水曜どうでしょう。",
        "hugh-americanpie-sanity": "アメリカン・パイを周回してる人、ちょっとだけ心配になる。",
        "mouth-hanayashiki-off": "最新映画情報の着地点が花やしきオフなの、自由すぎる。",
    }
    if clip_id in specific:
        return specific[clip_id]
    return f"{topic}、こういう話が一番おもしろい。"


def render_root(item: dict) -> str:
    first, second = _dialogue_lines(item["text"], item["topic"])
    punchline = _punchline(item)
    root = f"「{first}」\n\n「{second}」\n\n{punchline}\n\n{REELPAL_TAG}"

    # Keep the X post comfortably below the limit. Preserve the two-line dialogue,
    # punchline, and required tag while tightening long transcript fragments.
    if len(root) > 270:
        first = _clip_fragment(first, 54)
        second = _clip_fragment(second, 54)
        root = f"「{first}」\n\n「{second}」\n\n{punchline}\n\n{REELPAL_TAG}"
    if len(root) > 280:
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
