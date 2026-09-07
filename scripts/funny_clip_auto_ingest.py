#!/usr/bin/env python3
"""Admit transcript-grounded funny clips from newly published LISTEN episodes.

Gemini may select turn IDs and propose a hook, but it never supplies the posted
dialogue. Every dialogue line is copied back from LISTEN's transcript DOM and
validated for order, proximity, duplication, length, and an exact Spotify URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.episode_links import resolve_episode_links
from scripts.funny_clip_buffer import render_root
from scripts.funny_clip_quality_audit import load_canonical_bank, normalize
from scripts.listen_to_spotify_chapters import find_episode_urls
from scripts.listen_transcript_chapters import (
    DEFAULT_MODELS,
    episode_title,
    fetch_html,
    strip_json_fence,
)

ROOT = Path(__file__).resolve().parents[1]
AUTO_BANK_PATH = ROOT / "data" / "funny_clip_auto.json"
STATE_PATH = ROOT / "state_funny_ingest.json"
MAX_CLIPS_PER_EPISODE = 2
RETRY_HOURS = {
    "pending_transcript": 12,
    "pending_links": 6,
    "no_candidate": 72,
    "quarantined_asr": 168,
    "error": 12,
}
BANNED_HOOK_WORDS = (
    "面白すぎ",
    "おもしろすぎ",
    "好きすぎる",
    "ずっと聞いてられる",
    "最高すぎ",
    "神回",
)


@dataclass(frozen=True)
class TranscriptTurn:
    turn_id: str
    order: int
    segment_index: int
    text: str


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _episode_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _split_segment(text: str) -> list[str]:
    text = _clean(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])\s+", text)
    return [part for part in map(_clean, parts) if 2 <= len(part) <= 100]


def extract_transcript_turns(page_html: str) -> tuple[str, list[TranscriptTurn]]:
    soup = BeautifulSoup(page_html, "html.parser")
    title = episode_title(soup)
    turns: list[TranscriptTurn] = []
    seen_segments: set[int] = set()
    for node in soup.select("div[data-segment-index]"):
        raw_index = str(node.get("data-segment-index") or "").strip()
        if not raw_index.isdigit():
            continue
        segment_index = int(raw_index)
        if segment_index in seen_segments:
            continue
        seen_segments.add(segment_index)
        for text in _split_segment(node.get_text(" ", strip=True)):
            order = len(turns)
            turns.append(
                TranscriptTurn(
                    turn_id=f"T{order:04d}",
                    order=order,
                    segment_index=segment_index,
                    text=text,
                )
            )
    return title, turns


def transcript_health(turns: list[TranscriptTurn]) -> tuple[str, str]:
    total_chars = sum(len(turn.text) for turn in turns)
    if len(turns) < 30 or total_chars < 1500:
        return "pending_transcript", f"turns={len(turns)} chars={total_chars}"

    substantial = [normalize(turn.text) for turn in turns if len(normalize(turn.text)) >= 8]
    if len(substantial) < 20:
        return "pending_transcript", "too few substantial transcript turns"
    counts = Counter(substantial)
    repeated = sum(count - 1 for count in counts.values() if count >= 3)
    unique_ratio = len(counts) / len(substantial)
    if max(counts.values(), default=0) >= 8 or unique_ratio < 0.52 or repeated > len(substantial) * 0.35:
        return "quarantined_asr", (
            f"repetitive transcript: unique_ratio={unique_ratio:.2f} "
            f"max_repeat={max(counts.values(), default=0)}"
        )
    return "healthy", f"turns={len(turns)} chars={total_chars}"


def _turn_prompt(title: str, turns: list[TranscriptTurn]) -> str:
    rows = [
        f"[{turn.turn_id}|segment={turn.segment_index}] {turn.text}"
        for turn in turns
    ]
    return f"""You select real comic detours from a Japanese movie podcast transcript.
Episode: {title}

Return JSON only:
{{"clips":[{{"turn_ids":["T0001","T0002","T0003"],"topic":"short internal label","hook":"short Japanese hook"}}]}}

Rules:
- Return 0 to {MAX_CLIPS_PER_EPISODE} clips. An empty list is correct when no clearly funny standalone exchange exists.
- Select 3-6 turn IDs in ascending transcript order.
- Select one uninterrupted exchange. Every turn ID must be consecutive; never omit a turn from the middle.
- Keep each exchange local, spanning no more than 3 transcript segments.
- Prefer banter, misunderstandings, corrections, odd digressions, and a clear payoff.
- Do not select ordinary plot summary, serious film criticism, ads, opening theme lyrics, or self-praise.
- Do not rewrite, correct, merge, or invent dialogue. The system copies text from the selected IDs.
- The hook must be Japanese, at most 58 characters, factual, and grounded in selected dialogue.
- Never use hype such as 神回, 面白すぎる, 最高すぎる.

TRANSCRIPT TURNS:
""" + "\n".join(rows)


def generate_selection(
    title: str,
    turns: list[TranscriptTurn],
    models: list[str],
) -> tuple[dict, str]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed") from exc

    client = genai.Client(api_key=key)
    prompt = _turn_prompt(title, turns)
    failures: list[str] = []
    for model in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                payload = json.loads(strip_json_fence(response.text or ""))
                if not isinstance(payload, dict) or not isinstance(payload.get("clips"), list):
                    raise ValueError("unexpected clip selection payload")
                return payload, model
            except Exception as exc:
                failures.append(f"{model}: {type(exc).__name__}: {str(exc)[:180]}")
                if attempt == 0:
                    time.sleep(1)
        print(f"[WARN] Gemini model unavailable for funny ingest: {model}")
    raise RuntimeError("all Gemini models failed: " + " | ".join(failures[-6:]))


def _ngrams(value: str, size: int = 5) -> set[str]:
    value = normalize(value)
    return {value[i : i + size] for i in range(max(0, len(value) - size + 1))}


def _similar(left: str, right: str) -> bool:
    a = _ngrams(left)
    b = _ngrams(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.62


def _grounded_hook(proposed: object, dialogue: list[str]) -> str:
    hook = _clean(proposed)
    if hook and len(hook) <= 58 and not any(word in hook for word in BANNED_HOOK_WORDS):
        hook_grams = _ngrams(hook, 4)
        dialogue_grams = _ngrams(" ".join(dialogue), 4)
        if len(hook_grams & dialogue_grams) >= 2:
            return hook
    first = dialogue[0].rstrip("。！？!? ")[:24]
    last = dialogue[-1].rstrip("。！？!? ")[:24]
    return f"「{first}」から「{last}」へ。"


def materialize_clips(
    *,
    payload: dict,
    turns: list[TranscriptTurn],
    episode_title_value: str,
    source_url: str,
    spotify_url: str,
    existing_bank: list[dict],
    model: str,
) -> list[dict]:
    by_id = {turn.turn_id: turn for turn in turns}
    existing_texts = [
        " ".join(str(x) for x in item.get("dialogue", []))
        for item in existing_bank
        if isinstance(item.get("dialogue"), list)
    ]
    accepted: list[dict] = []
    episode_id = _episode_id(source_url)

    for selected in payload.get("clips", [])[:MAX_CLIPS_PER_EPISODE]:
        if not isinstance(selected, dict):
            continue
        ids = selected.get("turn_ids")
        if not isinstance(ids, list) or not 3 <= len(ids) <= 6:
            continue
        ids = [str(value) for value in ids]
        if len(set(ids)) != len(ids) or any(value not in by_id for value in ids):
            continue
        chosen = [by_id[value] for value in ids]
        orders = [turn.order for turn in chosen]
        segments = [turn.segment_index for turn in chosen]
        if orders != sorted(orders):
            continue
        if max(segments) - min(segments) > 3:
            continue
        if any(right - left != 1 for left, right in zip(orders, orders[1:])):
            continue

        dialogue = [turn.text for turn in chosen]
        chosen_ids = list(ids)
        chosen_segments = list(segments)
        while len(dialogue) > 3 and len(" / ".join(dialogue)) > 250:
            dialogue.pop()
            chosen_ids.pop()
            chosen_segments.pop()
        if len(" / ".join(dialogue)) > 250:
            continue
        ordinal = len(accepted) + 1
        clip_id = f"auto-funny-{episode_id}-{ordinal}"
        item: dict | None = None
        while len(dialogue) >= 3:
            hook = _grounded_hook(selected.get("hook"), dialogue)
            item = {
                "id": clip_id,
                "source": episode_title_value,
                "episode_title": episode_title_value,
                "topic": f"auto:{episode_id}:{ordinal}",
                "text": " / ".join(dialogue),
                "dialogue": list(dialogue),
                "hook": hook,
                "source_url": source_url,
                "spotify_url": spotify_url,
                "auto_generated": True,
                "transcript_proof": {
                    "turn_ids": list(chosen_ids),
                    "segment_indices": list(chosen_segments),
                    "model": model,
                },
            }
            if accepted:
                item["parent_id"] = accepted[0]["id"]
            try:
                rendered = render_root(item)
            except RuntimeError:
                item = None
                break
            rendered_turns = len(rendered.split("\n\n")) - 2
            if rendered_turns == len(dialogue):
                break
            dialogue = dialogue[:rendered_turns]
            chosen_ids = chosen_ids[:rendered_turns]
            chosen_segments = chosen_segments[:rendered_turns]

        if item is None or len(dialogue) < 3:
            continue
        combined = " ".join(dialogue)
        if any(_similar(combined, old) for old in existing_texts):
            continue
        accepted.append(item)
        existing_texts.append(combined)
    return accepted


def _load_auto_bank() -> list[dict]:
    try:
        data = json.loads(AUTO_BANK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        raise RuntimeError("funny_clip_auto.json must be a JSON array")
    return data


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("episodes", {}), dict):
        return {"episodes": {}}
    return {"episodes": data.get("episodes", {})}


def _attempt_due(record: dict, now: datetime) -> bool:
    status = str(record.get("status") or "")
    if status == "added":
        return False
    delay = RETRY_HOURS.get(status, 0)
    try:
        attempted = datetime.fromisoformat(str(record.get("attempted_at") or ""))
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return now >= attempted.astimezone(timezone.utc) + timedelta(hours=delay)


def _record(state: dict, url: str, status: str, detail: str, now: datetime) -> None:
    state["episodes"][url] = {
        "status": status,
        "detail": detail[:300],
        "attempted_at": now.astimezone(timezone.utc).isoformat(),
    }


def ingest(scan: int, limit: int, models: list[str]) -> dict[str, int]:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("[INFO] GEMINI_API_KEY is not configured; funny clip auto-ingest skipped")
        return {"attempted": 0, "episodes_added": 0, "clips_added": 0}

    now = datetime.now(timezone.utc)
    state = _load_state()
    auto_bank = _load_auto_bank()
    existing_bank = load_canonical_bank()
    known_urls = {str(item.get("source_url") or "") for item in existing_bank}
    urls = [url for url in find_episode_urls("https://listen.style/p/reelpal", scan) if url not in known_urls]

    attempted = 0
    episodes_added = 0
    clips_added = 0
    for url in urls:
        if attempted >= limit:
            break
        record = state["episodes"].get(url, {})
        if not _attempt_due(record, now):
            continue
        attempted += 1
        try:
            title, turns = extract_transcript_turns(fetch_html(url))
            health, detail = transcript_health(turns)
            if health != "healthy":
                _record(state, url, health, detail, now)
                print(f"[INFO] {health}: {url} ({detail})")
                continue
            try:
                links = resolve_episode_links(title=title, listen_url=url)
            except RuntimeError as exc:
                _record(state, url, "pending_links", str(exc), now)
                print(f"[INFO] pending_links: {url}")
                continue

            payload, model = generate_selection(title, turns, models)
            additions = materialize_clips(
                payload=payload,
                turns=turns,
                episode_title_value=title,
                source_url=url,
                spotify_url=links["spotify_url"],
                existing_bank=existing_bank,
                model=model,
            )
            if not additions:
                _record(state, url, "no_candidate", "no candidate passed deterministic gates", now)
                print(f"[INFO] no validated funny clip candidate: {url}")
                continue
            auto_bank.extend(additions)
            existing_bank.extend(additions)
            known_urls.add(url)
            episodes_added += 1
            clips_added += len(additions)
            _record(state, url, "added", f"clips={len(additions)} model={model}", now)
            print(f"[OK] auto-admitted {len(additions)} funny clip(s): {title}")
        except Exception as exc:
            _record(state, url, "error", f"{type(exc).__name__}: {exc}", now)
            print(f"[WARN] funny ingest error for {url}: {type(exc).__name__}: {exc}")

    AUTO_BANK_PATH.write_text(
        json.dumps(auto_bank, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "attempted": attempted,
        "episodes_added": episodes_added,
        "clips_added": clips_added,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=int, default=12)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--model", action="append", dest="models")
    args = parser.parse_args()
    ingest(max(1, args.scan), max(1, args.limit), args.models or DEFAULT_MODELS)


if __name__ == "__main__":
    main()
