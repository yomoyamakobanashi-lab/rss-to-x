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
from scripts.episode_links import render_episode_reply

BANK_PATHS = [
    ROOT / "data" / "funny_clip_posts.json",
    ROOT / "data" / "funny_clip_posts_archive.json",
    ROOT / "data" / "funny_clip_posts_archive_2.json",
    ROOT / "data" / "funny_clip_posts_archive_3.json",
    ROOT / "data" / "funny_clip_posts_all_episodes.json",
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

        dialogue = item.get("dialogue")
        if dialogue is not None:
            if not isinstance(dialogue, list):
                raise RuntimeError(f"dialogue must be a list: {clip_id}")
            dialogue = [_clean_fragment(x) for x in dialogue if _clean_fragment(x)]
            if len(dialogue) < 3:
                raise RuntimeError(f"explicit dialogue needs at least 3 turns: {clip_id}")
            if len(dialogue) > 8:
                raise RuntimeError(f"explicit dialogue has too many turns: {clip_id}")
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


def _clip_fragment(value: str, limit: int = 104) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip("、。 ") + "…"


def _sentences(value: str) -> list[str]:
    return [_clean_fragment(p) for p in re.split(r"[。！？!?]+", value) if _clean_fragment(p)]


def _quote_paraphrased_beat(value: str) -> str:
    """Quote the spoken proposition while keeping editorial narration outside."""
    clean = _clean_fragment(value)
    if not clean:
        return ""
    if "「" in clean and "」" in clean:
        return clean
    match = re.match(r"^(.*?)([—―]{2,}という[、,]?.*)$", clean)
    if match:
        spoken = match.group(1).rstrip(" 。、")
        narration = match.group(2).strip()
        return f"「{spoken}」{narration}"
    return f"「{clean}」"


def _dialogue_beats(text: str, topic: str) -> tuple[str, str]:
    """Legacy renderer for older summary-based banks."""
    original = str(text or "").strip()
    clean = _clean_fragment(original)
    quotes = [_clean_fragment(q) for q in re.findall(r"「([^」]+)」", original) if _clean_fragment(q)]

    if len(quotes) >= 2:
        first = f"「{quotes[0]}」"
        second = " / ".join(f"「{q}」" for q in quotes[1:3])
        return _clip_fragment(first), _clip_fragment(second)
    if len(quotes) == 1:
        first = f"「{quotes[0]}」"
        after = original.split("」", 1)[1].strip() if "」" in original else ""
        after_parts = [p for p in _sentences(after) if len(p) >= 8]
        if after_parts:
            second = "。".join(after_parts[:2])
        else:
            before = original.split("「", 1)[0]
            before_parts = [p for p in _sentences(before) if len(p) >= 8]
            second = before_parts[-1] if before_parts else f"{topic}の話から、また寄り道が始まる"
        return _clip_fragment(first), _clip_fragment(second)
    parts = _sentences(clean)
    if len(parts) >= 2:
        first = _quote_paraphrased_beat(parts[0])
        second = _quote_paraphrased_beat("。".join(parts[1:3]))
        return _clip_fragment(first), _clip_fragment(second)
    if parts:
        return _clip_fragment(_quote_paraphrased_beat(parts[0])), f"{topic}の話から、また別の話へ転がっていく。"
    return f"「{_clean_fragment(topic)}」", f"{topic}の話から、また別の話へ転がっていく。"


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
    explicit = _clean_fragment(item.get("hook") or "")
    if explicit:
        return explicit
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


def _explicit_dialogue(item: dict) -> list[str]:
    raw = item.get("dialogue")
    if not isinstance(raw, list):
        return []
    return [_clean_fragment(x) for x in raw if _clean_fragment(x)]


def _format_dialogue_line(value: str) -> str:
    """Keep transcript wording, but don't expose unreliable ASR speaker labels."""
    clean = _clean_fragment(value)
    clean = re.sub(r"^(?:マコ|オーマ|おだしょー|おだしょー)\s+", "", clean)
    clean = re.sub(r"「([^」]+)」", r"『\1』", clean)
    return f"「{clean}」"


def _render_explicit_dialogue(item: dict, hook: str) -> str:
    lines = [_format_dialogue_line(x) for x in _explicit_dialogue(item)]
    while len(lines) >= 3:
        root = "\n\n".join(lines + [hook, REELPAL_TAG])
        if len(root) <= 280:
            return root
        lines.pop()
    raise RuntimeError(f"explicit dialogue cannot fit without losing context: {item['id']}")


def render_root(item: dict) -> str:
    hook = _hook(item)
    explicit = _explicit_dialogue(item)
    if explicit:
        root = _render_explicit_dialogue(item, hook)
    else:
        first, second = _dialogue_beats(item["text"], item["topic"])
        root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"
        if len(root) > 280:
            second = _clip_fragment(second, 82)
            root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"
        if len(root) > 280:
            first = _clip_fragment(first, 82)
            root = f"{first}\n\n{second}\n\n{hook}\n\n{REELPAL_TAG}"
        if len(root) > 280:
            raise RuntimeError(f"rendered funny clip is too long: {item['id']} ({len(root)})")

    banned = ("面白すぎ", "おもしろすぎ", "好きすぎる", "ずっと聞いてられる", "最高すぎ", "神回")
    if any(word in hook for word in banned):
        raise RuntimeError(f"self-congratulatory funny clip hook rejected: {item['id']} -> {hook}")
    return root


def render_reply(item: dict) -> str:
    return render_episode_reply(
        title=item.get("episode_title") or item.get("source"),
        listen_url=item.get("source_url"),
        spotify_url=item.get("spotify_url"),
        intro="🎧 元の脱線を聴く",
    )


def save_state(state: dict, item: dict) -> None:
    used_ids = state["used_ids"] + [item["id"]]
    recent_sources = (state["recent_sources"] + [item["source"]])[-RECENT_SOURCE_WINDOW:]
    recent_topics = (state["recent_topics"] + [item["topic"]])[-RECENT_TOPIC_WINDOW:]
    STATE_PATH.write_text(
        json.dumps(
            {"used_ids": used_ids, "recent_sources": recent_sources, "recent_topics": recent_topics},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
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
        print(f"[INFO] funny clip bank exhausted: used={len(state['used_ids'])}, total={len(bank)}. No post sent; replenish with LISTEN-grounded clips from the archive.")
        return
    root = render_root(item)
    reply = render_reply(item)
    post_id = post_thread([root, reply])
    save_state(state, item)
    remaining = len(bank) - len(state["used_ids"]) - 1
    print(f"[OK] Buffer accepted funny clip thread: {post_id}; id={item['id']}; source={item['source']}; topic={item['topic']}; remaining={remaining}")


if __name__ == "__main__":
    main()
