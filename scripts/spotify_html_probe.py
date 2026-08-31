#!/usr/bin/env python3
from urllib.request import Request, urlopen
import base64, json, re

SHOW_ID = "4o8l9DJWMuwUht2pvkEytS"
SHOW_KEY = f"spotify:show:{SHOW_ID}"
HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"ja,en;q=0.8"}

def inspect(offset: int):
    url = f"https://open.spotify.com/show/{SHOW_ID}" + (f"?offset={offset}" if offset else "")
    with urlopen(Request(url, headers=HEADERS), timeout=30) as r:
        page = r.read().decode("utf-8", "replace")
    match = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', page)
    if not match:
        raise RuntimeError("initialState missing")
    raw = match.group(1).strip()
    raw += "=" * ((4 - len(raw) % 4) % 4)
    state = json.loads(base64.b64decode(raw).decode("utf-8"))
    show = (((state.get("entities") or {}).get("items") or {}).get(SHOW_KEY) or {})
    pages = show.get("pages") or {}
    print("OFFSET", offset, "NEXT", (pages.get("pagingInfo") or {}).get("nextOffset"), "TOTAL", pages.get("totalCount"))
    for wrapper in pages.get("items") or []:
        data = (((wrapper or {}).get("entity") or {}).get("data") or {})
        if data.get("__typename") == "Episode":
            print("EP", data.get("id"), "|||", data.get("name"))

inspect(0)
inspect(12)
inspect(24)
