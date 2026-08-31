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
seen: dict[str, str] = {}
try:
    driver.get(SHOW_URL)
    time.sleep(4)
    stable = 0
    last_total = 0
    for i in range(100):
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/episode/']")
        for a in anchors:
            href = a.get_attribute("href") or ""
            if "/episode/" not in href:
                continue
            text = (a.text or a.get_attribute("aria-label") or "").strip().replace("\n", " ")
            if href not in seen or (not seen[href] and text):
                seen[href] = text

        print("ITER", i, "DOM", len(anchors), "ACCUMULATED", len(seen))
        if len(seen) == last_total:
            stable += 1
        else:
            stable = 0
            last_total = len(seen)
        if len(seen) >= 120 or stable >= 14:
            break

        # Spotify uses an internal scroll container and virtualises episode rows.
        scrollable = driver.execute_script("""
            const els = [document.scrollingElement, ...document.querySelectorAll('*')];
            let best = null, bestOverflow = 0;
            for (const e of els) {
              if (!e) continue;
              const overflow = e.scrollHeight - e.clientHeight;
              if (overflow > bestOverflow && e.clientHeight > 300) {
                best = e; bestOverflow = overflow;
              }
            }
            return best;
        """)
        if scrollable is not None:
            driver.execute_script("arguments[0].scrollTop += Math.max(arguments[0].clientHeight * 0.78, 650);", scrollable)
        else:
            driver.execute_script("window.scrollBy(0, 900);")

        for button in driver.find_elements(By.TAG_NAME, "button"):
            label = (button.text or button.get_attribute("aria-label") or "").strip().lower()
            if any(word in label for word in ("もっと見る", "さらに表示", "show more", "see more", "すべて表示", "show all", "episodes")):
                try:
                    driver.execute_script("arguments[0].click();", button)
                except Exception:
                    pass
        time.sleep(0.8)

    for href, text in seen.items():
        print("LINK", href, "|||", text[:500])
    print("FINAL_COUNT", len(seen))
finally:
    driver.quit()
