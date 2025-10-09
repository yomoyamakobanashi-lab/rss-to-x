import os
import json
import time
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from requests_oauthlib import OAuth1
import re

STATE_FILE = "state.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 60       # 直後は各プラットフォームの反映待ち
MAX_RETRY_DAYS = 7        # 1週間は Spotify URL 出現を粘って待つ（過ぎたら mp3 で妥協するなら調整可）

RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s): json.dump(s, open(STATE_FILE, "w"))

def short_safe(text: str, n: int = MAX_TWEET_LEN) -> str:
    return (text[:n-1] + "…") if len(text) > n else text

def shorten_title(title: str, maxlen: int = TITLE_MAXLEN) -> str:
    t = (title or "").strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def post_to_x(text: str):
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")
    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    try:
        r = requests.post(
            "https://api.x.com/2/tweets",
            auth=auth,
            json={"text": text},
            headers={"Content-Type": "application/json"},
            timeout=20
        )
        return r.status_code, r.text
    except Exception as e:
        return 599, f"exception: {e}"

def entries_newest_first(parsed):
    try:
        return sorted(
            parsed.entries,
            key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
            reverse=True
        )
    except Exception:
        return list(parsed.entries)

def minutes_since(entry) -> float:
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t:
        return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

def days_since(entry) -> float:
    return minutes_since(entry) / 1440.0

def collect_text_blobs(entry) -> str:
    """Spotify URL/URI を拾うため、考えられる全テキストを結合"""
    chunks = []
    for k in ("id", "guid", "link", "title", "summary"):
        v = entry.get(k)
        if isinstance(v, str): chunks.append(v)
    sd = entry.get("summary_detail") or {}
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str): chunks.append(v)
    for c in entry.get("content", []):
        if isinstance(c, dict):
            v = c.get("value")
            if isinstance(v, str): chunks.append(v)
    for ln in entry.get("links", []):
        if isinstance(ln, dict):
            href = ln.get("href")
            if isinstance(href, str): chunks.append(href)
    return "\n".join(chunks)

def find_spotify_episode_url(entry) -> str | None:
    """
    1) open.spotify.com/episode/<ID> を全フィールドから直接検出
    2) spotify:episode:<ID> があれば open.spotify.com に組み立て
    """
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m:
        return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2:
        return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None

def pick_note_link(entry) -> str:
    return (entry.get("link") or "").strip()

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False

    for feed in cfg.get("feeds", []):
        if posted: break

        url = feed["url"]
        tmpl = feed["template"]
        ftype = feed.get("type", "")
        program = feed.get("program_name", "")

        parsed = feedparser.parse(url)

        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue

            age_min = minutes_since(entry)
            if age_min < FRESH_WAIT_MIN:
                print(f"[INFO] too fresh ({age_min:.0f}m) → skip for now: {(entry.get('title') or '').strip()}")
                continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)

            if ftype == "podcast":
                # ✅ Spotify の再生URLが見つかった時だけ投稿（安全策）
                sp = find_spotify_episode_url(entry)
                if not sp:
                    # 一定日数は再挑戦し続ける（stateに記録しない）
                    d = days_since(entry)
                    print(f"[INFO] Spotify URL not found yet (age {d:.1f}d). Will retry later: {title}")
                    # もし「何日以上経ったら mp3 で妥協」したければ、ここで enclosure を拾って投稿する分岐を追加可
                    continue
                link = sp
            else:
                # note などはそのまま
                link = pick_note_link(entry)

            text = tmpl.format(title=title, link=link, program=program)
            text = short_safe(text, MAX_TWEET_LEN)

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time()); save_state(state)
                print(f"[OK] posted: {title} ({status}) -> {link}")
                posted = True
                break
            else:
                print(f"[WARN] post failed ({status}): {body}")
                # 失敗は state に記録しない＝次回も再挑戦できる

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
