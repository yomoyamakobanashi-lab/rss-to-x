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
HOOKS_PATH = ROOT / "data" / "funny_hook_variants.json"
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
    return re.sub(r"\s+", " ", str(value or "")).strip(" 。、\n\t")


def _clip_fragment(value: str, limit: int = 112) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip("、。 \n") + "…"


def _sentence_units(value: str) -> list[str]:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    return [x.strip() for x in re.findall(r".+?(?:[。！？!?]+|$)", clean) if x.strip()]


def _dialogue_blocks(item: dict) -> tuple[str, str]:
    """Keep verified quote marks only; never turn paraphrase into fake dialogue."""
    text = str(item.get("text") or "").strip()
    clip_id = str(item.get("id") or "")

    specific = {
        "popcorn-koala": (
            "「コアラのマーチは絵を確認しながら食べたい」",
            "「でも運転中にそれをやると危ない。事故ったら警察も半笑いになるのでは」——という、映画と一切関係ない心配までしています。",
        ),
        "mandalorian-opening": (
            "3人収録で「インターミッション」の後に“ミッション、ミッション…”と続けた結果、",
            "3人目が何をすればいいのか完全に迷子になる。収録開始10秒で連携が崩れています。",
        ),
        "sheep-detective": (
            "「飼い主は最後まで生きてると思ってた」",
            "「殺された飼い主の犯人を羊が探す話」——同じ予告を見たはずなのに理解がだいぶ違う。",
        ),
        "omg-opening": (
            "インターミッションを始めるだけなのに「オーマイガー」を延々重ねる。",
            "文字起こしで見ると、開始直後から何をしている番組なのか余計に分からなくなる。",
        ),
    }
    if clip_id in specific:
        return specific[clip_id]

    quotes = [_clean_fragment(q) for q in re.findall(r"「([^」]+)」", text) if _clean_fragment(q)]
    if len(quotes) >= 2:
        prefix = text.split("「", 1)[0].strip()
        suffix = text.rsplit("」", 1)[1].strip() if "」" in text else ""
        first = f"「{quotes[0]}」"
        if prefix:
            first = f"{prefix}\n{first}"
        second = f"「{quotes[1]}」"
        if len(quotes) >= 3:
            second += f" / 「{quotes[2]}」"
        if suffix:
            second += f"\n{suffix}"
        return _clip_fragment(first), _clip_fragment(second)

    if len(quotes) == 1:
        before, after = text.split("「", 1)
        quoted, tail = after.split("」", 1)
        first = f"「{_clean_fragment(quoted)}」"
        if before.strip():
            first = f"{before.strip()}\n{first}"
        tail_units = _sentence_units(tail)
        if tail_units:
            second = "".join(tail_units[:2])
        else:
            second = f"{item['topic']}の話から、また別の話へ転がっていく。"
        return _clip_fragment(first), _clip_fragment(second)

    # No quote marks in the grounded bank copy means it is a paraphrase/summary.
    # Keep it as prose instead of fabricating dialogue quotation marks.
    units = _sentence_units(text)
    if len(units) >= 2:
        first = units[0]
        second = "".join(units[1:3])
        return _clip_fragment(first), _clip_fragment(second)
    if units:
        return _clip_fragment(units[0]), _clip_fragment(f"{item['topic']}の話から、また別の話へ転がっていく。")
    return _clip_fragment(str(item["topic"])), "このあと話はさらに別方向へ転がっていく。"


def _load_hooks() -> list[str]:
    try:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        hooks = []
    hooks = [_clean_fragment(x) for x in hooks if _clean_fragment(x)] if isinstance(hooks, list) else []
    return hooks or [
        "今日の脱線トークはどこにいくのか。",
        "映画ポッドキャストと名乗っていいのだろうか。",
        "このあと本題に戻れるのか。",
    ]


def _hook(item: dict) -> str:
    clip_id = str(item.get("id") or "")
    specific = {
        "popcorn-koala": "コアラのマーチから映画の話に戻れるのか。",
        "mandalorian-opening": "収録開始10秒で、この先がちょっと不安になる。",
        "sheep-detective": "同じ予告を見ても、ここまで理解が分かれる。",
        "omg-opening": "映画の話が始まる前から、だいぶ寄り道している。",
    }
    if clip_id in specific:
        return specific[clip_id]

    hooks = _load_hooks()
    selector = sum(ord(ch) for ch in clip_id) % len(hooks)
    return hooks[selector]


def render_root(item: dict) -> str:
    first, second = _dialogue_blocks(item)
    hook = _hook(item)
    root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"

    if len(root) > 280:
        second = _clip_fragment(second, 82)
        root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"
    if len(root) > 280:
        first = _clip_fragment(first, 82)
        root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"
    if len(root) > 280:
        raise RuntimeError(f"rendered funny clip is too long: {item['id']} ({len(root)})")

    banned = ("面白すぎ", "おもしろすぎ", "好きすぎる", "ずっと聞いてられる")
    if any(word in hook for word in banned):
        raise RuntimeError(f"self-congratulatory funny clip hook rejected: {item['id']} -> {hook}")
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
