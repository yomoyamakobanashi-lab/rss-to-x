import os, json, time, hashlib, requests
import feedparser
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s): json.dump(s, open(STATE_FILE,"w"))

def short(text, n=260):  # Xの文字数ガード
    return (text[:n-1] + "…") if len(text) > n else text

def post_to_x(text):
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secretsを確認）")

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    r = requests.post(
        "https://api.x.com/2/tweets",
        auth=auth,
        json={"text": text},
        timeout=30
    )
    if r.status_code >= 300:
        raise RuntimeError(f"X投稿失敗: {r.status_code} {r.text}")

def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    changed = False

    for feed in cfg["feeds"]:
        url = feed["url"]
        tmpl = feed["template"]
        program = feed.get("program_name","")
        parsed = feedparser.parse(url)

        for entry in reversed(parsed.entries[:5]):  # 直近5件チェック
            uid_base = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_base)).encode("utf-8")).hexdigest()
            if uid in state:
                continue

            title = (entry.get("title") or "").strip()
            link  = (entry.get("link") or "").strip()
            text  = tmpl.format(title=title, link=link, program=program)
            text  = short(text, 260)

            post_to_x(text)
            state[uid] = int(time.time())
            changed = True

    if changed:
        save_state(state)

if __name__ == "__main__":
    main()
