import os
import json
import time
import hashlib
import requests
import feedparser
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w"))

def short_safe(text: str, n: int = 240) -> str:
    """日本語/絵文字を考慮して余裕を持った最終カット（既定240字）"""
    return (text[:n-1] + "…") if len(text) > n else text

def shorten_title(title: str, maxlen: int = 90) -> str:
    """長いタイトルを先に短縮（既定90字）"""
    t = (title or "").strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def post_to_x(text: str):
    """OAuth1（User context）で v2 /2/tweets に投稿。失敗時も例外は投げず戻り値で返す。"""
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        # ここだけは致命的なので例外
        raise RuntimeError("Xのキーが未設定です（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    try:
        r = requests.post(
            "https://api.x.com/2/tweets",   # 安定の v2 エンドポイント
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
        # 最新→古い の順で最大8件だけ試す
        for entry in entries_newest_first(parsed)[:8]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue  # 既に投稿済み

            # タイトルは先に短縮、本文も最終ガード
            title = shorten_title(entry.get("title") or "", maxlen=90)
            link = (entry.get("link") or "").strip()
            text = tmpl.format(title=title, link=link, program=program)
            text = short_safe(text, 240)

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time())
                save_state(state)
                print(f"[OK] posted: {title} ({status})")
                posted = True
                break
            else:
                # 重複/文字数/レート/権限などで失敗しても、ジョブは落とさず次候補へ
                print(f"[WARN] post failed ({status}): {body}")
                # state には記録しない＝次回も再挑戦できる

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
