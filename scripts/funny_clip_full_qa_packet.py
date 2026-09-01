#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.funny_clip_quality_audit import load_canonical_bank, normalize

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "funny_clip_transcript_snapshot.json"
OUTPUT = ROOT / "funny_clip_full_qa_packet.json"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def find_context(text: str, turns: list[str], radius: int = 650) -> str:
    if not text:
        return ""
    positions: list[int] = []
    for turn in turns:
        phrase = clean(turn)
        if not phrase:
            continue
        pos = text.find(phrase)
        if pos >= 0:
            positions.append(pos)
            continue
        compact = re.sub(r"\s+", "", phrase)
        if len(compact) >= 10:
            seed = compact[: min(22, len(compact))]
            match = re.search(r"\s*".join(map(re.escape, seed)), text)
            if match:
                positions.append(match.start())
    if not positions:
        return text[: min(1300, len(text))]
    return text[max(0, min(positions) - radius): min(len(text), max(positions) + radius)]


def main() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    transcripts = {
        normalize(item.get("episode_title", "")): str(item.get("text") or "")
        for item in snapshot.get("episodes", [])
    }
    bank = load_canonical_bank()
    rows = []
    for item in bank:
        dialogue = [clean(x) for x in item.get("dialogue", []) if clean(x)]
        transcript = transcripts.get(normalize(item.get("episode_title", "")), "")
        rows.append({
            "id": item.get("id"),
            "parent_id": item.get("parent_id"),
            "episode_title": item.get("episode_title"),
            "source_url": item.get("source_url"),
            "spotify_url": item.get("spotify_url"),
            "topic": item.get("topic"),
            "dialogue": dialogue,
            "hook": clean(item.get("hook", "")),
            "context": find_context(transcript, dialogue),
        })
    payload = {
        "summary": {
            "clips": len(rows),
            "base_clips": sum(1 for x in rows if not x["parent_id"]),
            "extra_clips": sum(1 for x in rows if x["parent_id"]),
            "episodes": len({x["episode_title"] for x in rows}),
        },
        "clips": rows,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
