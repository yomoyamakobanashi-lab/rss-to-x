#!/usr/bin/env python3
"""Reliable daily dispatcher for three varied ReelPal posting slots.

Each invocation publishes at most one root post/thread.  The workflow calls it
repeatedly, so a delayed GitHub cron can catch the next missing slot without
posting several roots at once.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state_daily_content.json"
JST = ZoneInfo("Asia/Tokyo")
KEEP_DAYS = 35

SLOTS = (
    ("engagement", 8, 10),
    ("feature", 17, 10),
    ("funny", 21, 20),
)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"days": {}}


def save_state(state: dict) -> None:
    days = state.setdefault("days", {})
    for old_date in sorted(days)[:-KEEP_DAYS]:
        days.pop(old_date, None)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_due_slot(now: datetime, state: dict) -> str | None:
    local = now.astimezone(JST)
    date_key = local.date().isoformat()
    posted = set((state.get("days", {}).get(date_key) or {}).get("posted_slots", []))
    for slot, hour, minute in SLOTS:
        if slot in posted:
            continue
        if (local.hour, local.minute) >= (hour, minute):
            return slot
    return None


def _run_module(module: str) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if output:
        print(output)
    if result.returncode != 0:
        raise RuntimeError(f"{module} failed with exit code {result.returncode}")
    return "[OK] Buffer accepted" in output, output


def _engagement_modules(weekday: int) -> list[str]:
    # Questions and deeper readings alternate through the week.  The survey is
    # a durable final fallback, so the daily slot does not silently disappear
    # when a finite editorial bank is exhausted.
    if weekday in {0, 2, 4}:
        return ["scripts.quick_reply_buffer", "scripts.discussion_buffer", "scripts.survey_buffer"]
    if weekday in {1, 3, 5}:
        return ["scripts.discussion_buffer", "scripts.quick_reply_buffer", "scripts.survey_buffer"]
    return ["scripts.survey_buffer", "scripts.quick_reply_buffer", "scripts.discussion_buffer"]


def execute_slot(slot: str, now: datetime) -> str:
    if slot == "engagement":
        modules = _engagement_modules(now.astimezone(JST).weekday())
    elif slot == "feature":
        # A transcript/chapter-grounded social pack wins when one is due.
        # Otherwise every day still gets an archive spotlight.  Saturday uses
        # the three-episode digest for a visibly different shape.
        modules = ["scripts.social_pack_buffer"]
        if now.astimezone(JST).weekday() == 5:
            modules.append("scripts.weekly_digest_buffer")
        modules.append("scripts.random_episode_buffer")
    elif slot == "funny":
        modules = ["scripts.funny_clip_spotify"]
    else:
        raise ValueError(f"unknown slot: {slot}")

    for module in modules:
        posted, _ = _run_module(module)
        if posted:
            return module
    raise RuntimeError(f"required daily slot produced no post: {slot}")


def mark_posted(state: dict, now: datetime, slot: str, module: str) -> None:
    local = now.astimezone(JST)
    date_key = local.date().isoformat()
    day = state.setdefault("days", {}).setdefault(
        date_key,
        {"posted_slots": [], "posted_at": {}, "content_types": {}},
    )
    if slot not in day["posted_slots"]:
        day["posted_slots"].append(slot)
    day["posted_at"][slot] = local.isoformat()
    day["content_types"][slot] = module
    save_state(state)


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(JST)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", help="ISO timestamp used for deterministic dry-runs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = parse_now(args.now)
    if not args.dry_run:
        from scripts.social_pack_autofill import enqueue_latest

        enqueue_latest(now)

    state = load_state()
    slot = next_due_slot(now, state)
    if slot is None:
        print(f"[INFO] no due daily content slot at {now.astimezone(JST).isoformat()}")
        return 0
    if args.dry_run:
        print(f"[DRY RUN] next slot={slot}; date={now.astimezone(JST).date().isoformat()}")
        return 0

    module = execute_slot(slot, now)
    mark_posted(state, now, slot, module)
    print(f"[OK] daily content slot recorded: slot={slot}; module={module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
