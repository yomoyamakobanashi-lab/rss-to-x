#!/usr/bin/env python3
import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

ROOT = Path(__file__).resolve().parents[1]
BANKS = [
    ROOT / "data" / "funny_clip_posts.json",
    ROOT / "data" / "funny_clip_posts_archive.json",
    ROOT / "data" / "funny_clip_posts_archive_2.json",
    ROOT / "data" / "funny_clip_posts_archive_3.json",
]
SHOW_ID = "4o8l9DJWMuwUht2pvkEytS"

def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = value.replace("replay", "")
    value = re.sub(r"[\s　]+", "", value)
    value = re.sub(r"[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]/\\]", "", value)
    return value

def score(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()

sources = {}
for path in BANKS:
    for item in json.loads(path.read_text(encoding="utf-8")):
        source = str(item.get("source") or "").strip()
        title = str(item.get("episode_title") or "").strip()
        if source and title:
            sources[source] = title

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1440,1200")
opts.add_argument("--lang=ja-JP")

driver = webdriver.Chrome(options=opts)
try:
    for index, (source, title) in enumerate(sorted(sources.items()), 1):
        query = f'"{title}"'
        url = "https://open.spotify.com/search/" + quote(query, safe="")
        driver.get(url)
        time.sleep(2.2)
        candidates = []
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/episode/']"):
            href = a.get_attribute("href") or ""
            if "/episode/" not in href:
                continue
            text = (a.text or a.get_attribute("aria-label") or "").strip().replace("\n", " ")
            # Search-result cards often put title in a descendant rather than anchor text.
            if not text:
                try:
                    text = a.find_element(By.XPATH, "ancestor::*[self::div or self::li][1]").text.strip().replace("\n", " ")
                except Exception:
                    pass
            if href not in {x[0] for x in candidates}:
                candidates.append((href, text, score(title, text)))
        candidates.sort(key=lambda x: x[2], reverse=True)
        print("SOURCE", index, source, "|||", title)
        if not candidates:
            print("NO_CANDIDATES")
            continue
        for href, text, similarity in candidates[:5]:
            print("CANDIDATE", f"{similarity:.3f}", href, "|||", text[:500])
finally:
    driver.quit()
