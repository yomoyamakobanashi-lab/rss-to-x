#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "analytics" / "x_metrics_history.json"
OUTPUT_PATH = ROOT / "data" / "winner_candidates.json"

MIN_AGE_HOURS = 72
MAX_AGE_DAYS = 60
MIN_IMPRESSIONS = 80
MULTIPLIER = 1.5
ELIGIBLE_KINDS = {
    "quick_reply",
    "discussion",
    "funny_clip",
    "audiogram",
    "archive_weekend",
    "social_pack_three_hooks",
    "social_pack_host_split",
    "social_pack_episode_hook",
    "winner_remix",
}


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_history() -> list[dict]:
    try:
        raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    return [x for x in raw.values() if isinstance(x, dict)]


def main() -> None:
    rows = load_history()
    now = datetime.now(timezone.utc)
    lower = now - timedelta(days=MAX_AGE_DAYS)
    upper = now - timedelta(hours=MIN_AGE_HOURS)

    eligible = []
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind not in ELIGIBLE_KINDS:
            continue
        published = parse_dt(row.get("publishedAt"))
        if not published or not (lower <= published <= upper):
            continue
        text = str(row.get("text") or "").strip()
        if not text or "http://" in text or "https://" in text:
            continue
        metrics = row.get("metrics") or {}
        try:
            impressions = float(metrics.get("impressions", 0) or 0)
        except (TypeError, ValueError):
            impressions = 0.0
        if impressions < MIN_IMPRESSIONS:
            continue
        eligible.append((row, impressions, published))

    # Compare against the same format where possible; this avoids declaring a new-episode spike
    # a winner against low-reach conversational posts.
    by_kind: dict[str, list[float]] = {}
    for row, imp, _ in eligible:
        by_kind.setdefault(str(row.get("kind")), []).append(imp)

    global_values = [imp for _, imp, _ in eligible]
    global_median = statistics.median(global_values) if global_values else 0.0
    candidates = []

    for row, imp, published in eligible:
        kind = str(row.get("kind"))
        peers = by_kind.get(kind, [])
        baseline = statistics.median(peers) if len(peers) >= 3 else global_median
        if baseline <= 0:
            continue
        ratio = imp / baseline
        if ratio < MULTIPLIER:
            continue
        metrics = row.get("metrics") or {}
        candidates.append({
            "post_id": str(row.get("id") or ""),
            "kind": kind,
            "text": str(row.get("text") or "").strip(),
            "published_at": published.isoformat(),
            "impressions": int(round(imp)),
            "baseline_median_impressions": round(float(baseline), 2),
            "ratio_to_baseline": round(float(ratio), 2),
            "comments": int(round(float(metrics.get("comments", 0) or 0))),
            "reposts": int(round(float(metrics.get("reposts", 0) or 0))),
            "external_link": row.get("externalLink"),
        })

    candidates.sort(
        key=lambda x: (x["ratio_to_baseline"], x["impressions"]), reverse=True
    )
    OUTPUT_PATH.write_text(
        json.dumps(candidates[:30], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[OK] winner candidates={len(candidates[:30])}; "
        f"eligible={len(eligible)}; global_median={global_median:.1f}"
    )


if __name__ == "__main__":
    main()
