#!/usr/bin/env python3
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

SHOW_URL = "https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS"
opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1440,1200")
opts.add_argument("--lang=ja-JP")

driver = webdriver.Chrome(options=opts)
try:
    driver.get(SHOW_URL)
    time.sleep(4)
    last_count = -1
    stable = 0
    for i in range(80):
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/episode/']")
        hrefs = list(dict.fromkeys(a.get_attribute("href") for a in anchors if a.get_attribute("href")))
        print("ITER", i, "EPISODE_LINKS", len(hrefs))
        if len(hrefs) == last_count:
            stable += 1
        else:
            stable = 0
            last_count = len(hrefs)
        if len(hrefs) >= 120 or stable >= 8:
            break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.2)
        # Spotify occasionally renders a show-more control instead of infinite scroll.
        for button in driver.find_elements(By.TAG_NAME, "button"):
            label = (button.text or "").strip().lower()
            if label in {"もっと見る", "さらに表示", "show more", "see more", "すべて表示", "show all"}:
                try:
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(1)
                except Exception:
                    pass
    anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/episode/']")
    seen = set()
    for a in anchors:
        href = a.get_attribute("href") or ""
        if "/episode/" not in href or href in seen:
            continue
        seen.add(href)
        text = (a.text or a.get_attribute("aria-label") or "").strip().replace("\n", " ")
        print("LINK", href, "|||", text[:500])
    print("FINAL_COUNT", len(seen))
finally:
    driver.quit()
