#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Extend the existing X metrics collector with dynamic queue-backed post types."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import x_metrics_report as base

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CLASSIFY = base.classify_post


def load_social_pack_map() -> dict[str, str]:
    path = ROOT / "data" / "social_pack_queue.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not text or not kind:
            continue
        out[text] = "winner_remix" if kind == "winner_remix" else f"social_pack_{kind}"
    return out


def load_audiogram_captions() -> set[str]:
    path = ROOT / "data" / "audio_clip_queue.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    return {
        str(item.get("caption") or "").strip()
        for item in data
        if isinstance(item, dict) and str(item.get("caption") or "").strip()
    }


SOCIAL_PACK_KIND = load_social_pack_map()
AUDIOGRAM_CAPTIONS = load_audiogram_captions()


def classify_post(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("映画好きに聞きたい。"):
        return "trend_discovery"
    dynamic = SOCIAL_PACK_KIND.get(raw)
    if dynamic:
        return dynamic
    if raw in AUDIOGRAM_CAPTIONS:
        return "audiogram"
    return ORIGINAL_CLASSIFY(raw)


def main() -> None:
    base.classify_post = classify_post
    base.main()


if __name__ == "__main__":
    main()
