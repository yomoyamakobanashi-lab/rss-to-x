import os, json, time, hashlib, requests, feedparser
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
def save_state(s): json.dump(s, open(STATE_FILE,"w"))

def short_safe(text, n=240):
    return (text[:n-1] + "…") if len(text) > n else text

def shorten_title(title, maxlen=90):
    t = title.strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def post_to_x(text):
    api_key = os.getenv("X_API_KEY"); api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN"); access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secretsを確認）")
    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    r = requests.post(
        "https://api.x.com/2/tweets",
        auth=auth,
        json={"text": text},
        headers={"Content-Type":"application/json"},
        timeout=20
    )
    return r.status_code, r.text

def entries_newest_first(parsed):
    # published_parsed / updated_parsed があれば新しい順にソート、なければそのまま
    try:
        e = sorted(parsed.entries, key=lambda x: getattr(x, "published_parsed", getattr(x,"updated_parsed", None)) or 0, reverse=True)
        return e
    except Exception:
        return list(parsed.entries)

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False

    for feed in cfg["feeds"]:
        if posted: break
        url = feed["url"]; tmpl = feed["template"]; program = feed.get("program_name","")
        parsed = feedparser.parse(url)

        # ✅ 最新→古い の順で確認（直近8件まで）
        for entry in entries_newest_first(parsed)[:8]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue

            title = shorten_title(entry.get("title") or "", maxlen=90)  # ✅ タイトルを先に短縮
            link  = (entry.get("link") or "").strip()
            text  = tmpl.format(title=title, link=link, program=program)
            text  = short_safe(text, 240)  # ✅ 全文も最終ガード

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time()); save_state(state)
                print(f"[OK] posted (newest-first): {title}")
                posted = True
                break
            else:
                print(f"[WARN] post failed ({status}): {body}")
                # 失敗は state に保存しない＝次回も再挑戦できる

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
