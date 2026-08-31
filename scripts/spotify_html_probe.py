#!/usr/bin/env python3
from urllib.request import Request, urlopen
from pathlib import Path
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

show_url = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
page = fetch(show_url)
print("SHOW_IDS", ids(page))
for term in ["accessToken", "access_token", "clientId", "client_id", 'id="session"', "session", "initialState"]:
    positions = [m.start() for m in re.finditer(term, page, re.I)]
    print("TERM", term, "COUNT", len(positions))
    for pos in positions[:4]:
        snippet = page[max(0, pos-500):pos+1600].replace("\n", " ")
        # Never dump a complete token into logs; retain enough structure to identify the field.
        snippet = re.sub(r'("accessToken"\s*:\s*")[^"]+', r'\1<REDACTED>', snippet, flags=re.I)
        print("SNIP", term, snippet[:2100])
