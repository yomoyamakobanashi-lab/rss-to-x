#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEGACY = DATA / "funny_clip_legacy_canonical.json"
SNAPSHOT = ROOT / "funny_clip_transcript_snapshot.json"
OUTPUT = ROOT / "funny_clip_legacy_qa_packet.json"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_title(value: str) -> str:
    value = clean(value).lower()
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”\"'’]", "", value)
    return value


def load_overrides() -> dict[str, dict]:
    patches: dict[str, dict] = {}
    for path in sorted(DATA.glob("funny_clip_quality_overrides*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for clip_id, patch in data.items():
            if isinstance(patch, dict):
                patches[str(clip_id)] = patch
    return patches


def find_context(text: str, turns: list[str], radius: int = 1000) -> tuple[str, int]:
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
        compact = re.sub(r"\s+", "", phrase)
        if len(compact) >= 10:
            seed = compact[: min(22, len(compact))]
            match = re.search(r"\s*".join(map(re.escape, seed)), text)
            if match:
                hits.append(match.start())
    if not hits:
        return text[: min(2200, len(text))], exact
    return text[max(0, min(hits)-radius): min(len(text), max(hits)+radius)], exact


def main() -> None:
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    patches = load_overrides()
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    episodes = snap.get("episodes", [])
    by_url = {str(x.get("source_url") or ""): str(x.get("text") or "") for x in episodes}
    by_title = {norm_title(x.get("episode_title", "")): str(x.get("text") or "") for x in episodes}
    packet = []
    for original in legacy:
        item = dict(original)
        item.update(patches.get(str(item.get("id") or ""), {}))
        turns = [clean(x) for x in item.get("dialogue", []) if clean(x)]
        transcript = by_url.get(str(item.get("source_url") or ""), "")
        matched_by = "url" if transcript else ""
        if not transcript:
            transcript = by_title.get(norm_title(item.get("episode_title", "")), "")
            matched_by = "title" if transcript else ""
        context, exact = find_context(transcript, turns)
        packet.append({
            "id": item.get("id"),
            "episode_title": item.get("episode_title"),
            "source_url": item.get("source_url"),
            "dialogue": turns,
            "hook": clean(item.get("hook", "")),
            "dialogue_turns": len(turns),
            "exact_turns_found_in_listen_text": exact,
            "listen_text_chars": len(transcript),
            "matched_by": matched_by,
            "context": context,
        })
    payload = {
        "summary": {
            "episodes": len(packet),
            "matched_transcripts": sum(1 for x in packet if x["listen_text_chars"]),
            "matched_by_url": sum(1 for x in packet if x["matched_by"] == "url"),
            "all_turns_exact": sum(1 for x in packet if x["dialogue_turns"] and x["dialogue_turns"] == x["exact_turns_found_in_listen_text"]),
            "zero_turns_exact": sum(1 for x in packet if x["exact_turns_found_in_listen_text"] == 0),
        },
        "episodes": packet,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
