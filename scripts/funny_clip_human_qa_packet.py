#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BANK = DATA / "funny_clip_posts_all_episodes.json"
OVERRIDE_PATHS = [
    DATA / "funny_clip_quality_overrides.json",
    DATA / "funny_clip_quality_overrides_2.json",
]
SNAPSHOT = ROOT / "funny_clip_transcript_snapshot.json"
OUTPUT = ROOT / "funny_clip_human_qa_packet.json"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_overrides() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in OVERRIDE_PATHS:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if isinstance(value, dict):
                result[str(key)] = value
    return result


def norm_title(value: str) -> str:
    value = clean(value).lower()
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’]", "", value)
    return value


def find_context(text: str, turns: list[str], radius: int = 900) -> tuple[str, int]:
    if not text:
        return "", 0
    hits: list[int] = []
    exact = 0
    for turn in turns:
        phrase = clean(turn)
        if not phrase:
            continue
        pos = text.find(phrase)
        if pos >= 0:
            exact += 1
            hits.append(pos)
            continue
        # ASR punctuation/spacing can differ. Try a stable 12–24 character seed.
        compact = re.sub(r"\s+", "", phrase)
        seeds = []
        if len(compact) >= 12:
            seeds.append(compact[: min(24, len(compact))])
            seeds.append(compact[-min(20, len(compact)):])
        for seed in seeds:
            pattern = r"\s*".join(map(re.escape, seed))
            match = re.search(pattern, text)
            if match:
                hits.append(match.start())
                break
    if not hits:
        return text[: min(1800, len(text))], exact
    start = max(0, min(hits) - radius)
    end = min(len(text), max(hits) + radius)
    return text[start:end], exact


def main() -> None:
    bank = json.loads(BANK.read_text(encoding="utf-8"))
    patches = load_overrides()
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    transcripts = {
        norm_title(item.get("episode_title", "")): item.get("text", "")
        for item in snapshot.get("episodes", [])
    }

    packet: list[dict] = []
    for item in bank:
        merged = dict(item)
        merged.update(patches.get(str(item.get("id") or ""), {}))
        dialogue = [clean(x) for x in merged.get("dialogue", []) if clean(x)]
        transcript = transcripts.get(norm_title(merged.get("episode_title", "")), "")
        context, exact_count = find_context(transcript, dialogue)
        packet.append({
            "id": merged.get("id"),
            "episode_title": merged.get("episode_title"),
            "source_url": merged.get("source_url"),
            "spotify_url": merged.get("spotify_url"),
            "dialogue": dialogue,
            "hook": clean(merged.get("hook", "")),
            "dialogue_turns": len(dialogue),
            "exact_turns_found_in_listen_text": exact_count,
            "listen_text_chars": len(transcript),
            "context": context,
            "has_quality_override": str(item.get("id") or "") in patches,
        })

    payload = {
        "summary": {
            "episodes": len(packet),
            "with_quality_override": sum(1 for x in packet if x["has_quality_override"]),
            "all_turns_exact": sum(
                1 for x in packet
                if x["dialogue_turns"] and x["exact_turns_found_in_listen_text"] == x["dialogue_turns"]
            ),
            "zero_turns_exact": sum(1 for x in packet if x["exact_turns_found_in_listen_text"] == 0),
        },
        "episodes": packet,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
