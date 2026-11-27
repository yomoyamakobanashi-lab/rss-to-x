"""Source-checked weekday editorial bank; no generated-at-publication prose."""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from buffer_client import _ensure_reelpal_tag
from scripts.episode_links import resolve_episode_links

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "data/mako_film_stories.json"
SHOW = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
CATEGORIES = ("映画怪異録", "銀幕陰謀論", "シネフィルの処方箋", "映画心理学講義", "銀幕人文学", "映画の舞台裏", "映画トリビア研究所")

def render(item):
    parts = list(item["posts"])
    # Each bibliographic entry gets its own reply if the combined sources
    # would exceed the conservative Buffer/X limit.
    refs = ["参考資料：" + s["name"] + "\n" + s["url"] for s in item["sources"]]
    if item.get("episode_guid"):
        link = resolve_episode_links(guid=item["episode_guid"])["spotify_url"]
        cta = "関連回もぜひ。映画から人間・文化・社会を語るポッドキャスト Reel Friends in Tokyo。\n🎧 " + link
    else:
        cta = "映画の見方が変わる話を、番組でも。\n🎧 Reel Friends in Tokyo（リルパル）\n" + SHOW
    parts += refs + [cta]
    return [_ensure_reelpal_tag(f"{i+1}/{len(parts)}\n{p}") for i,p in enumerate(parts)]

def load_bank(require_full=False):
    rows = json.loads(BANK.read_text(encoding="utf-8"))
    ids, topics = set(), set()
    for row in rows:
        if row["id"] in ids or row["topic_key"] in topics:
            raise ValueError("Duplicate story identity: " + row["id"])
        ids.add(row["id"]); topics.add(row["topic_key"])
        weekday = row["weekday"]
        if type(weekday) is not int or not 0 <= weekday <= 6 or row["category"] != CATEGORIES[weekday]:
            raise ValueError("Invalid weekday/category: " + row["id"])
        if row["review_status"] != "source_checked" or not row.get("checked_on"):
            raise ValueError("Unreviewed item: " + row["id"])
        if not 3 <= len(row["posts"]) <= 6 or not all(isinstance(p,str) and p.strip() for p in row["posts"]):
            raise ValueError("Invalid story body: " + row["id"])
        if "#まこの映画小話" not in row["posts"][0] or "http" in row["posts"][0]:
            raise ValueError("Invalid root: " + row["id"])
        if not row["sources"]:
            raise ValueError("No references: " + row["id"])
        for s in row["sources"]:
            u = urlsplit(s["url"])
            if u.scheme != "https" or not u.hostname or u.username or u.password or not s.get("supports") or not s.get("name"):
                raise ValueError("Invalid source record: " + row["id"])
        render(row)
    counts = Counter(r["weekday"] for r in rows)
    if require_full and any(counts[d] < 20 for d in range(7)):
        raise ValueError(f"Initial release gate: require 20 checked stories per weekday; current={dict(counts)}")
    return rows

def select(rows, weekday, used):
    return next((r for r in rows if r["weekday"] == weekday and r["id"] not in used), None)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--require-full",action="store_true")
    args=p.parse_args()
    rows=load_bank(args.require_full)
    print("Source-checked bank:", dict(sorted(Counter(r["weekday"] for r in rows).items())))
