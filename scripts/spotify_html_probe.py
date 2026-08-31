#!/usr/bin/env python3
from urllib.request import Request, urlopen
from pathlib import Path
import base64
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

m = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', page)
if not m:
    raise SystemExit("initialState missing")
raw = m.group(1).strip()
pad = "=" * ((4 - len(raw) % 4) % 4)
state = json.loads(base64.b64decode(raw + pad).decode("utf-8"))
print("STATE_TOP_KEYS", list(state.keys()))

# Print structural paths relevant to show/episode pagination without dumping bulky payloads.
def walk(obj, path="root", depth=0):
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = str(k)
            p = f"{path}.{kp}"
            lk = kp.lower()
            if any(t in lk for t in ("episode", "page", "offset", "cursor", "total", "next", "limit")):
                if isinstance(v, (str, int, float, bool)) or v is None:
                    val = repr(v)[:500]
                elif isinstance(v, list):
                    val = f"<list len={len(v)}>"
                elif isinstance(v, dict):
                    val = f"<dict keys={list(v.keys())[:25]}>"
                else:
                    val = f"<{type(v).__name__}>"
                print("PATH", p, "=", val)
            walk(v, p, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            walk(v, f"{path}[{i}]", depth + 1)
walk(state)

serialized = json.dumps(state, ensure_ascii=False)
uris = sorted(set(re.findall(r'spotify:episode:([A-Za-z0-9]{22})', serialized)))
print("STATE_EPISODE_URIS", len(uris), uris)

show_key = "spotify:show:4o8l9DJWMuwUht2pvkEytS"
items = (((state.get("entities") or {}).get("items") or {}))
show = items.get(show_key)
if isinstance(show, dict):
    print("SHOW_ENTITY_KEYS", list(show.keys()))
    for k, v in show.items():
        if any(t in str(k).lower() for t in ("episode", "page", "total", "next", "offset", "cursor")):
            print("SHOW_FIELD", k, json.dumps(v, ensure_ascii=False)[:8000])
