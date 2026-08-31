#!/usr/bin/env python3
from urllib.request import Request, urlopen
import base64, json, re

SHOW_ID = "4o8l9DJWMuwUht2pvkEytS"
HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"ja,en;q=0.8"}
url = f"https://open.spotify.com/show/{SHOW_ID}"
with urlopen(Request(url, headers=HEADERS), timeout=30) as r:
    page = r.read().decode("utf-8", "replace")

match = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', page)
raw = match.group(1).strip()
raw += "=" * ((4 - len(raw) % 4) % 4)
state = json.loads(base64.b64decode(raw).decode("utf-8"))

req = state.get("request")
print("REQUEST", json.dumps(req, ensure_ascii=False)[:20000])

scripts = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', page, re.I)
print("SCRIPT_COUNT", len(scripts))
for src in scripts:
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://open.spotify.com" + src
    print("SCRIPT", src)

# Inspect a limited number of bundles for episode pagination/pathfinder operation names.
for src in scripts[:18]:
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://open.spotify.com" + src
    try:
        with urlopen(Request(src, headers=HEADERS), timeout=30) as r:
            js = r.read().decode("utf-8", "replace")
    except Exception as exc:
        print("JSERR", src, repr(exc))
        continue
    hits = []
    for term in ["nextOffset", "ContextEpisodePage", "pathfinder", "queryShow", "showEpisodes", "episodePages", "PodcastEpisodes"]:
        pos = js.find(term)
        if pos >= 0:
            hits.append(term)
            snippet = js[max(0,pos-700):pos+1400]
            print("HIT", term, src, snippet[:2100])
    if hits:
        print("HITS", src, hits)
