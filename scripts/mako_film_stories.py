"""Source-checked weekday editorial bank; no generated-at-publication prose."""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from datetime import date

import requests

from buffer_client import BufferError, _ensure_reelpal_tag
from scripts.episode_links import resolve_episode_links

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "data/mako_film_stories.json"
SHOW = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
CATEGORIES = ("映画怪異録", "銀幕陰謀論", "シネフィルの処方箋", "映画心理学講義", "銀幕人文学", "映画の舞台裏", "映画トリビア研究所")

# Only explicit reuse permission, not merely a public poster URL, qualifies.
POSTER_LICENSES = {"public_domain", "CC0", "CC-BY", "CC-BY-SA", "rights_holder_permission"}
POSTER_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org", "collectionapi.metmuseum.org", "images.metmuseum.org", "cdn.eso.org"}
MEDIA_USER_AGENT = "ReelPalFilmStories/1.1 (https://github.com/yomoyamakobanashi-lab/rss-to-x; editorial image accessibility)"
_BLOCKED_MEDIA_HOSTS = set()

def _asset_url(item, field):
    poster = item.get(field)
    if poster is None:
        return None
    if not isinstance(poster, dict):
        raise ValueError("Invalid poster record: " + item["id"])
    status = poster.get("status")
    if status == "pending":
        return None
    if status != "rights_checked":
        raise ValueError("Unknown poster review status: " + item["id"])
    required = ("film_title", "year") if field == "poster" else ("asset_title", "relation", "asset_kind")
    for key in (*required, "source_url", "image_url", "license",
                "rights_evidence_url", "rights_basis", "credit", "checked_on"):
        if not str(poster.get(key) or "").strip():
            raise ValueError("Incomplete poster rights record: " + item["id"] + "/" + key)
    if field == "related_image" and poster["asset_kind"] not in {"archival_photo", "historical_art", "book_cover", "film_still", "scientific_image", "diagram", "related_object"}:
        raise ValueError("Invalid related image kind: " + item["id"])
    date.fromisoformat(poster["checked_on"])
    if poster["license"] not in POSTER_LICENSES:
        raise ValueError("Unapproved poster license: " + item["id"])
    for key in ("source_url", "image_url", "rights_evidence_url"):
        u = urlsplit(poster[key])
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("Invalid poster URL: " + item["id"])
    u = urlsplit(poster["image_url"])
    if u.hostname not in POSTER_HOSTS or u.port not in (None, 443):
        raise ValueError("Unapproved poster file host: " + item["id"])
    return poster["image_url"]

def poster_url(item):
    return _asset_url(item, "poster")

def visual_assets(item):
    """Prefer the actual poster, then a separately cleared relevant image."""
    return [(field, url) for field in ("poster", "related_image") if (url := _asset_url(item, field))]

def _reachable_image(item, url):
    """Check media before reserving a slot; no redirects or blind post retries."""
    if not url:
        return None
    host = urlsplit(url).hostname
    if host in _BLOCKED_MEDIA_HOSTS:
        return None
    try:
        with requests.get(url, stream=True, allow_redirects=False, timeout=12,
                          headers={"User-Agent": MEDIA_USER_AGENT}) as res:
            if res.status_code in (429, 503):
                _BLOCKED_MEDIA_HOSTS.add(host)
                print(f"[WARN] {host} throttled; no further requests to host this run; Retry-After={res.headers.get('Retry-After', 'unspecified')}")
                return None
            if res.status_code != 200 or res.headers.get("Content-Type", "").split(";")[0].lower() not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("HTTP status or media type not suitable")
            size = int(res.headers.get("Content-Length", "0"))
            if size > 5 * 1024 * 1024:
                raise ValueError("Image exceeds conservative 5 MB limit")
            head = next(res.iter_content(32), b"")
            if not (head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")):
                raise ValueError("Not a supported image signature")
        return url
    except (requests.RequestException, ValueError, StopIteration) as exc:
        print(f"[WARN] image unavailable for {item['id']}; no image-less submission: {exc}")
        return None

def reachable_poster(item):
    return _reachable_image(item, poster_url(item))

def reachable_visual(item):
    for field, url in visual_assets(item):
        if _reachable_image(item, url):
            return field, url
    return None

def render(item, *, visual_field=None):
    parts = list(item["posts"])
    # Each bibliographic entry gets its own reply if the combined sources
    # would exceed the conservative Buffer/X limit.
    refs = ["参考資料：" + s["name"] + "\n" + s["url"] for s in item["sources"]]
    if item.get("episode_guid"):
        link = resolve_episode_links(guid=item["episode_guid"])["spotify_url"]
        cta = "関連回もぜひ。映画から人間・文化・社会を語るポッドキャスト Reel Friends in Tokyo。\n🎧 " + link
    else:
        cta = "映画の見方が変わる話を、番組でも。\n🎧 Reel Friends in Tokyo（リルパル）\n" + SHOW
    assets = dict(visual_assets(item))
    field = visual_field or next(iter(assets), None)
    if field:
        if field not in assets:
            raise ValueError("Unapproved visual selected: " + item["id"])
        asset = item[field]
        label = "ポスター資料：" if field == "poster" else "関連資料画像（映画ポスターではありません）："
        credit = label + asset["credit"] + "／" + asset["license"]
        source = asset["source_url"]
        combined = credit + "\n" + source
        # Reserve space for numbering and the mandatory tag. Never truncate attribution.
        try:
            _ensure_reelpal_tag("99/99\n" + combined)
        except BufferError:
            refs.extend([credit, "画像出典：\n" + source])
        else:
            refs.append(combined)
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
        try:
            for field, _ in visual_assets(row):
                render(row, visual_field=field)
            render(row)
        except BufferError as exc:
            raise ValueError(f"Invalid rendered story {row['id']}: {exc}") from exc
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
