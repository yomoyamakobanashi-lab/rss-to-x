import os, json, time, hashlib, requests, feedparser
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s): json.dump(s, open(STATE_FILE,"w"))

def short_safe(text, n=240):  # 日本語/絵文字を考慮して余裕を持つ
    return (text[:n-1] + "…") if len(text) > n else text

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

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False  # 今回の実行で1件でも成功したか
    for feed in cfg["feeds"]:
        if posted: break  # 1回の実行で最大1件のみ投稿
        url = feed["url"]; tmpl = feed["template"]; program = feed.get("program_name","")
        parsed = feedparser.parse(url)

        for entry in reversed(parsed.entries[:5]):  # 新しめから最大5件チェック
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue

            title = (entry.get("title") or "").strip()
            link  = (entry.get("link") or "").strip()
            text  = short_safe(tmpl.format(title=title, link=link, program=program))

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time())
                save_state(state)
                print(f"[OK] posted: {title}")
                posted = True
                break
            else:
                # 失敗は警告ログに残して次の候補へ（ジョブは落とさない）
                print(f"[WARN] post failed ({status}): {body}")
                continue

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
