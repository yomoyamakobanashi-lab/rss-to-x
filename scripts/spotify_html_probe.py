#!/usr/bin/env python3
from urllib.request import Request, urlopen
from urllib.parse import quote
from pathlib import Path
import html as htmlmod
import json
import re

ROOT = Path(__file__).resolve().parents[1]
BANKS = [
    ROOT / "data" / "funny_clip_posts.json",
    ROOT / "data" / "funny_clip_posts_archive.json",
    ROOT / "data" / "funny_clip_posts_archive_2.json",
    ROOT / "data" / "funny_clip_posts_archive_3.json",
]
HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"ja,en;q=0.8"}

def fetch(url):
    with urlopen(Request(url, headers=HEADERS), timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def ids(page):
    return list(dict.fromkeys(re.findall(r'/episode/([A-Za-z0-9]{22})', page)))

sources = {}
for path in BANKS:
    for item in json.loads(path.read_text(encoding="utf-8")):
        source = str(item.get("source") or "").strip()
        title = str(item.get("episode_title") or "").strip()
        if source and title:
            sources[source] = title
print("UNIQUE_SOURCES", len(sources))
for source, title in sorted(sources.items()):
    print("SOURCE", source, "|||", title)

show_url = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
page = fetch(show_url)
print("SHOW_IDS", ids(page))

# Probe Spotify's public HTML search only as an internal discovery mechanism.
# Nothing here is ever used as a listener-facing link.
for source, title in sorted(sources.items()):
    search_url = "https://open.spotify.com/search/" + quote(title, safe="")
    try:
        search_page = fetch(search_url)
    except Exception as exc:
        print("SEARCH_ERROR", source, repr(exc))
        continue
    candidates = ids(search_page)
    print("SEARCH", source, "CANDIDATES", candidates[:12])
    for episode_id in candidates[:4]:
        needle = "/episode/" + episode_id
        pos = search_page.find(needle)
        snippet = htmlmod.unescape(re.sub(r"<[^>]+>", " ", search_page[max(0,pos-500):pos+1500]))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        print("CANDIDATE", source, episode_id, "|||", snippet[:500])
