#!/usr/bin/env python3
from urllib.request import Request, urlopen
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

url = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
req = Request(url, headers={"User-Agent":"Mozilla/5.0","Accept-Language":"ja,en;q=0.8"})
with urlopen(req, timeout=30) as r:
    page = r.read().decode("utf-8", "replace")
print("HTML_BYTES", len(page))
links = []
for m in re.finditer(r'<a[^>]+href="/episode/([A-Za-z0-9]{22})"[^>]*>\s*<h4[^>]*data-testid="episodeTitle"[^>]*>(.*?)</h4>', page, re.I | re.S):
    episode_id = m.group(1)
    title_html = m.group(2)
    title = htmlmod.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
    links.append((episode_id, title))
print("PUBLIC_EPISODES", len(links))
for episode_id, title in links:
    print("EPISODE", episode_id, "|||", title)

for term in ["offset", "cursor", "showMore", "show-all", "see all", "episodes", "next"]:
    positions = [m.start() for m in re.finditer(term, page, re.I)]
    print("TERM", term, "COUNT", len(positions))
    for pos in positions[:2]:
        snippet = page[max(0, pos-350):pos+900].replace("\n", " ")
        print("SNIP", term, snippet[:1250])
