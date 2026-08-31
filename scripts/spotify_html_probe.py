#!/usr/bin/env python3
from urllib.request import Request, urlopen
import re

url = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
req = Request(url, headers={"User-Agent":"Mozilla/5.0","Accept-Language":"ja,en;q=0.8"})
with urlopen(req, timeout=30) as r:
    html = r.read().decode("utf-8", "replace")
print("html_bytes", len(html))
for pattern in [r"spotify:episode", r"open\.spotify\.com/episode", r"episode/[A-Za-z0-9]{22}", r"__NEXT_DATA__", r"initialState", r"entityUniqueId"]:
    print(pattern, len(re.findall(pattern, html, re.I)))
for needle in ["spotify:episode", "/episode/", "__NEXT_DATA__", "episodeUnionV2"]:
    pos = html.find(needle)
    print("NEEDLE", needle, "POS", pos)
    if pos >= 0:
        print(html[max(0,pos-600):pos+1800].replace("\n"," ")[:2400])
