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
FRESH_WAIT_MIN = 60  # 直後ポストは各プラットフォーム反映待ち

# 正規表現（SpotifyエピソードURLとURI）
RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w"))

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

def _text_fields(entry):
    """Spotify URL/URI を拾うため、考えられる全テキストを列挙"""
    fields = []
    for k in ("id", "guid", "link", "title", "summary"):
        v = entry.get(k)
        if isinstance(v, str):
            fields.append(v)
    # summary_detail
    sd = entry.get("summary_detail") or {}
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str):
            fields.append(v)
    # content[]
    for c in entry.get("content", []):
        if isinstance(c, dict):
            v = c.get("value")
            if isinstance(v, str):
                fields.append(v)
    # links[]
    for ln in entry.get("links", []):
        if isinstance(ln, dict):
            href = ln.get("href")
            if isinstance(href, str):
                fields.append(href)
    return "\n".join(fields)

def find_spotify_episode_url(entry) -> str | None:
    """
    1) すべてのテキストから open.spotify.com/episode/<ID> を直接サーチ
    2) それでも無ければ spotify:episode:<ID> から再生URLを組み立て
    無ければ None
    """
    blob = _text_fields(entry)

    m = RE_SPOTIFY_URL.search(blob)
    if m:
        # 既に完全な再生URLがどこかに埋まっている
        return f"https://open.spotify.com/episode/{m.group(1)}"

    m2 = RE_SPOTIFY_URI.search(blob)
    if m2:
        return f"https://open.spotify.com/episode/{m2.group(1)}"

    return None

def pick_best_link(entry) -> str:
    """
    優先度：
      1) どこかに埋まっている SpotifyエピソードURL（強化版検出）
      2) enclosure（mp3 直リンク）
      3) entry.link（ただし /play/ や creators/podcasters は避けたい）
    """
    # 1) Spotify再生URLを総当たりで検出
    sp = find_spotify_episode_url(entry)
    if sp:
        return sp

    # 2) mp3
    for enc in entry.get("enclosures", []):
        href = (enc.get("href") or "").strip()
        if href:
            return href

    # 3) fallback（アンカーの /play/ や creators/podcasters は避ける）
    link = (entry.get("link") or "").strip()
    if any(s in link for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
        # links の他候補を探してみる
        for ln in entry.get("links", []):
            href = (ln.get("href") or "").strip()
            if href and not any(s in href for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
                return href
    return link

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False

    for feed in cfg.get("feeds", []):
        if posted:
            break

        url = feed["url"]
        tmpl = feed["template"]
        program = feed.get("program_name", "")

        parsed = feedparser.parse(url)

        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue

            # 直後は各ディレクトリ取り込み待ち
            if minutes_since(entry) < FRESH_WAIT_MIN:
                print(f"[INFO] too fresh → skip for now: {(entry.get('title') or '').strip()}")
                continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)
            best_link = pick_best_link(entry)
            text = tmpl.format(title=title, link=best_link, program=program)
            text = short_safe(text, MAX_TWEET_LEN)

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time())
                save_state(state)
                print(f"[OK] posted: {title} ({status}) -> {best_link}")
                posted = True
                break
            else:
                print(f"[WARN] post failed ({status}): {body}")

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
