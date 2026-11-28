"""Single automatic X publishing entry point. JST root-post cap: one story + one companion."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import feedparser
import requests
import podcast_poster_buffer as podcast
from buffer_client import post_thread, _ensure_reelpal_tag
from scripts.verify_funny_publication import verify
from scripts import mako_film_stories as stories
from scripts import funny_clip_spotify as funny

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "state_mako_publish.json"
JST = ZoneInfo("Asia/Tokyo")
RSS = "https://anchor.fm/s/10422ca68/podcast/rss"

def load_ledger():
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    if not isinstance(data.get("days"), dict) or not isinstance(data.get("used_story_ids"), list):
        raise RuntimeError("Invalid publishing ledger; refusing to publish")
    return data

def write_ledger(state):
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LEDGER)

def run_git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)

def persist_reservation(state):
    write_ledger(state)
    run_git("add", str(LEDGER.relative_to(ROOT)))
    run_git("commit", "-m", "Reserve X publishing slot [skip ci]")
    # No force or rebase here. If the branch moved, stop before external writes.
    run_git("push", "origin", "HEAD:main")

def day_slots(state, date):
    return state["days"].setdefault(date, {})

def reserve_and_send(state, date, slot, kind, key, texts, *, image_url=None):
    if slot not in ("story", "companion"):
        raise ValueError("Invalid slot")
    if datetime.now(JST).date().isoformat() != date:
        raise RuntimeError("JST day changed; retry with current date")
    day = day_slots(state,date)
    if slot in day:
        raise RuntimeError("Slot already reserved: " + slot)
    if any(r.get("key") == key for d in state["days"].values() for r in d.values() if isinstance(r,dict)):
        raise RuntimeError("Content already reserved: " + key)
    texts = [_ensure_reelpal_tag(t) for t in texts]
    rec = {"kind":kind,"key":key,"status":"reserved","reserved_at":datetime.now(JST).isoformat()}
    day[slot] = rec
    if kind == "story":
        state["used_story_ids"].append(key)
    # Commit reservation before calling Buffer. A timeout or runner crash never
    # triggers a blind automatic retry of a potentially published thread.
    persist_reservation(state)
    post_id = post_thread(texts, image_url=image_url) if image_url else post_thread(texts)
    rec.update(status="accepted", buffer_id=post_id)
    write_ledger(state)
    print(f"[OK] Buffer accepted {slot}: {kind} {post_id}",flush=True)
    return post_id

def due_story(now, state, rows):
    if now.hour < 7 or "story" in day_slots(state,now.date().isoformat()):
        return None
    return stories.select(rows,now.weekday(),set(state["used_story_ids"]))

def due_visual_story(now, state, rows):
    """Skip unlicensed or unavailable images before any durable reservation."""
    if now.hour < 7 or "story" in day_slots(state,now.date().isoformat()):
        return None
    used = set(state["used_story_ids"])
    for item in rows:
        if item["weekday"] != now.weekday() or item["id"] in used:
            continue
        visual = stories.reachable_visual(item)
        if visual:
            field, url = visual
            return item, field, url
    return None

def newest_feed():
    response=requests.get(RSS,timeout=30,headers={"User-Agent":"ReelPalDaily/1.0"})
    response.raise_for_status()
    parsed=feedparser.parse(response.content)
    entries=podcast.entries_newest_first(parsed)
    if not entries:
        raise RuntimeError("RSS empty; cannot safely choose companion")
    entry=entries[0]
    uid_src=entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
    if not uid_src or not podcast.entry_timestamp(entry):
        raise RuntimeError("Invalid latest episode identity or date")
    uid=hashlib.sha256((RSS+"|"+str(uid_src)).encode("utf-8")).hexdigest()
    return parsed,entry,uid

def companion(now,state):
    date=now.date().isoformat()
    if "companion" in day_slots(state,date):
        return
    parsed,entry,uid=newest_feed()
    age=podcast.minutes_since(entry)
    published_date=datetime.fromtimestamp(podcast.entry_timestamp(entry),timezone.utc).astimezone(JST).date()
    posted=podcast.load_state()
    if published_date >= now.date() and uid in posted:
        # Respect an announcement made by the previous workflow before cutover.
        day_slots(state,date)["companion"]={"kind":"podcast","key":"podcast:"+uid,"status":"previously_recorded"}
        write_ledger(state)
        return
    if 0 <= age <= podcast.MAX_CONTENT_AGE_HOURS*60 and uid not in posted:
        if age < podcast.MIN_AGE_MINUTES:
            return
        key="podcast:"+uid
        if any(r.get("key")==key for d in state["days"].values() for r in d.values() if isinstance(r,dict)):
            raise RuntimeError("Announcement already reserved; reconcile Buffer before retry")
        with patch.object(podcast.feedparser,"parse",return_value=parsed), patch.object(podcast,"post_thread",side_effect=lambda texts:reserve_and_send(state,date,"companion","podcast",key,texts)):
            podcast.main()
        if "companion" not in day_slots(state,date):
            raise RuntimeError("Eligible announcement was not submitted; retain companion slot")
        return
    if published_date >= now.date():
        # Do not spend the slot while a new entry is future-dated.
        return
    if (now.hour,now.minute) < (19,30):
        return
    if os.getenv("FUNNY_CLIP_DRY_RUN"):
        raise RuntimeError("Unexpected dry-run flag on publishing runner")
    # Use canonical bank selection and exact episode-link logic, without a
    # second independent workflow. Reserve by actual clip ID/cycle.
    bank=funny.base.load_bank()
    rotation,_=funny.base.rollover_state(bank,funny.base.load_state())
    # A reserved clip may have reached Buffer even if its rotation state was lost.
    # Exclude all reserved IDs in the current cycle, without resending them.
    prefix=f"funny:{rotation.get('cycle',1)}:"
    reserved_ids={r.get("key","")[len(prefix):] for d in state["days"].values() for r in d.values() if isinstance(r,dict) and r.get("key","").startswith(prefix)}
    rotation["used_ids"]=list(dict.fromkeys(rotation["used_ids"]+sorted(reserved_ids)))
    item=funny.base.pick_clip(bank,rotation)
    if item is None:
        print("[SKIP] no funny clip ready")
        return
    key=f"funny:{rotation.get('cycle',1)}:{item['id']}"
    texts=[*funny.base.render_posts(item),funny.base.render_reply(item)]
    post_id=reserve_and_send(state,date,"companion","funny",key,texts)
    funny.base.save_state(rotation,item,post_id)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--dry-run",action="store_true")
    args=p.parse_args()
    state=load_ledger()
    rows=stories.load_bank()
    now=datetime.now(JST)
    if args.dry_run:
        print("Current JST:",now.isoformat())
        print("Existing slots:",day_slots(state,now.date().isoformat()))
        item=due_story(now,state,rows)
        if item:
            print("\n---\n".join(stories.render(item)))
        return
    if os.getenv("GITHUB_REF")!="refs/heads/main" or os.getenv("REELPAL_UNIFIED_PUBLISH")!="1":
        raise RuntimeError("Live posting allowed only in the unified main workflow")
    start = state.get("start_jst")
    if not start or now.date().isoformat() < start:
        print("[SKIP] waiting for coordinated JST cutover")
        return
    stories.load_bank(require_full=True)
    # A free Buffer queue may support only one pending thread at a time.
    # Confirm the previous accepted root before sending another thread.
    errors=[]
    date=now.date().isoformat()
    def pending():
        return any(r.get("status") in ("reserved", "accepted")
                   for day in state["days"].values() for r in day.values())
    def reconcile():
        # A queue entry can remain unresolved across midnight. Never ignore it
        # just because the current JST date is different.
        for original_date, day in list(state["days"].items()):
            for slot, rec in list(day.items()):
                if rec.get("status")=="reserved":
                    errors.append(f"Ambiguous reservation {original_date}/{slot}; reconcile manually before further posting")
                elif rec.get("status")=="accepted":
                    if not rec.get("buffer_id"):
                        errors.append(f"Missing Buffer ID {original_date}/{slot}; reconcile manually")
                        continue
                    try:
                        post=verify(rec["buffer_id"],attempts=2,delay=5)
                        rec.update(status="root_confirmed",external_link=post["externalLink"],
                                   confirmed_at=datetime.now(JST).isoformat())
                        # If confirmation crosses midnight, conservatively spend
                        # the matching slot today too. The actual sent date is
                        # not returned by the verification query.
                        if original_date != date:
                            day_slots(state,date).setdefault(slot, {
                                "kind":rec["kind"], "key":"carryover:"+rec["key"],
                                "status":"carryover_confirmed", "original_date":original_date,
                                "buffer_id":rec["buffer_id"]})
                        write_ledger(state)
                    except Exception as exc:
                        errors.append(f"publication unconfirmed {rec['buffer_id']}: {exc}")
    reconcile()
    if not pending():
        try:
            companion(now,state)
        except Exception as exc:
            errors.append(f"companion: {exc}")
        reconcile()
    if not pending():
        try:
            ready=due_visual_story(now,state,rows)
            if ready:
                item, field, image_url=ready
                reserve_and_send(state,date,"story","story",item["id"],stories.render(item,visual_field=field),image_url=image_url)
            elif now.hour>=7 and "story" not in day_slots(state,date):
                raise RuntimeError("No unused story with a reachable rights-cleared visual; story slot held without reservation")
        except Exception as exc:
            errors.append(f"story: {exc}")
        reconcile()
    if errors:
        raise RuntimeError("; ".join(errors))

if __name__=="__main__":
    main()
