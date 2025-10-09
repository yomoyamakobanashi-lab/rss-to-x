import os, json, time, hashlib, requests, feedparser
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state_note.json"

MAX_TWEET_LEN = 240
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 10   # noteは反映早いので短めでもOK

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w"))

def shorten_title(title, maxlen=TITLE_MAXLEN):
    t = (title or "").strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def entry_timestamp(entry) -> int:
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    try:
        return int(time.mktime(t)) if t else 0
    except Exception:
        return 0

def minutes_since(entry) -> float:
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t:
        return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

def entries_newest_first(parsed):
    try:
        return sorted(parsed.entries,
                      key=lambda x: getattr(x,"published_parsed",getattr(x,"updated_parsed",None)) or 0,
                      reverse=True)
    except Exception:
        return list(parsed.entries)

def post_to_x(text: str):
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")
    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    try:
        r = requests.post("https://api.x.com/2/tweets",
                          auth=auth,
                          json={"text": text},
                          headers={"Content-Type":"application/json"},
                          timeout=20)
        return r.status_code, r.text
    except Exception as e:
        return 599, f"exception: {e}"

# 日本語キー対応の本文生成（URLは末尾固定で切らない）
def render_body_without_link(template: str, title: str, program: str) -> str:
    body = template
    for k in ("{title}","{タイトル}"):
        body = body.replace(k, title)
    for k in ("{program}","{番組名}"):
        body = body.replace(k, program)
    for k in ["{link}","{URL}","{Url}","{url}","{記事URL}"]:
        body = body.replace(k, "").rstrip()
    return body.replace("\r","").rstrip()

def compose_text(template: str, title: str, program: str, link: str, limit: int = MAX_TWEET_LEN) -> str:
    link = (link or "").strip()
    url_part = ("\n"+link) if link else ""
    body = render_body_without_link(template, title, program)
    candidate = (body + url_part).strip()
    if len(candidate) <= limit:
        return candidate
    for tag in [" #ReelPal"," #リルパル"," #note"]:
        if len(candidate) <= limit: break
        body = body.replace(tag, "")
        candidate = (body + url_part).strip()
    if len(candidate) <= limit:
        return candidate
    for L in [90,70,50,30,15]:
        short_title = (title[:L-1]+"…") if len(title)>L else title
        body_short = render_body_without_link(template, short_title, program)
        candidate = (body_short + url_part).strip()
        if len(candidate) <= limit:
            return candidate
    minimal = (program + url_part).strip() if link else program
    return minimal if len(minimal) <= limit else link

def main():
    cfg   = json.load(open("feeds.json"))
    state = load_state()

    candidates = []

    for feed in cfg.get("feeds", []):
        if feed.get("type") != "note":
            continue

        url   = feed["url"]
        tmpl  = feed["template"]
        program = feed.get("program_name","")

        parsed = feedparser.parse(url)

        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state: continue
            if minutes_since(entry) < FRESH_WAIT_MIN: continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)
            link  = (entry.get("link") or "").strip()
            if not link: continue

            text = compose_text(tmpl, title, program, link, limit=MAX_TWEET_LEN)
            ts   = entry_timestamp(entry)
            candidates.append({"ts": ts, "uid": uid, "text": text})

    if not candidates:
        print("[INFO] no eligible note candidates this run")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    status, body = post_to_x(chosen["text"])
    if status < 300:
        state[chosen["uid"]] = int(time.time())
        save_state(state)
        print(f"[OK] posted note: {status}")
    else:
        print(f"[WARN] note post failed ({status}): {body}")

if __name__ == "__main__":
    main()
