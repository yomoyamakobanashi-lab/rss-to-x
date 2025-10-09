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
MAX_TWEET_LEN = 240          # 絵文字/日本語を考慮して余裕を持つ
TITLE_MAXLEN   = 90           # タイトル事前短縮
CHECK_ITEMS    = 8            # 最新から最大ここまで試す
FRESH_WAIT_MIN = 60           # 直後ポストは各プラットフォーム反映待ち

SPOTIFY_EP_RE = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

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
    """OAuth1（User context）で v2 /2/tweets に投稿。戻り値で成否を返す。"""
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
    """RSSを新しい順に。published_parsed/updated_parsed が無い場合はそのまま。"""
    try:
        return sorted(
            parsed.entries,
            key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
            reverse=True
        )
    except Exception:
        return list(parsed.entries)

def minutes_since(entry) -> float:
    """エピソード公開からの経過分（不明なら大きな数を返す＝待たずにOK）"""
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t:
        return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

def extract_spotify_episode_id(entry) -> str | None:
    """GUIDやIDから spotify:episode:XXXX を抜き出し、IDを返す。"""
    cand = entry.get("id") or entry.get("guid") or ""
    m = SPOTIFY_EP_RE.search(str(cand))
    if m:
        return m.group(1)
    # linksに "spotify:episode:..." が載るケースにも対応
    for ln in entry.get("links", []):
        href = (ln.get("href") or "")
        m2 = SPOTIFY_EP_RE.search(href)
        if m2:
            return m2.group(1)
    return None

def pick_best_link(entry) -> str:
    """
    優先度：
      1) open.spotify.com/episode/... が links にあれば最優先
      2) GUID/ID から spotify:episode:ID を抽出して open.spotify.com/episode/ID を生成
      3) enclosure（mp3 直リンク）
      4) entry.link（creators/podcasters ドメイン含む）
    """
    # 1) links配列に episode の再生URLがあるか
    for ln in entry.get("links", []):
        href = (ln.get("href") or "").strip()
        if "open.spotify.com/episode/" in href:
            return href

    # 2) GUID/ID から episode ID を生成
    ep_id = extract_spotify_episode_id(entry)
    if ep_id:
        return f"https://open.spotify.com/episode/{ep_id}"

    # 3) enclosure（音源直リンク）
    for enc in entry.get("enclosures", []):
        href = (enc.get("href") or "").strip()
        if href:
            return href

    # 4) fallback
    return (entry.get("link") or "").strip()

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False  # 今回1件でも成功したか

    for feed in cfg.get("feeds", []):
        if posted:
            break  # 1回の実行で最大1件のみ投稿（安定運用）

        url = feed["url"]
        tmpl = feed["template"]
        program = feed.get("program_name", "")

        parsed = feedparser.parse(url)

        # 最新→古い の順で最大 CHECK_ITEMS 件だけ試す
        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue  # 既に投稿済み

            # 反映遅延対策：直後は少し待つ
            age_min = minutes_since(entry)
            if age_min < FRESH_WAIT_MIN:
                print(f"[INFO] too fresh ({age_min:.0f}m) → skip for now: {(entry.get('title') or '').strip()}")
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
                # 失敗は記録しない＝次回も再挑戦できる

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
