import os, json, time, hashlib, re, requests, feedparser
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state_podcast.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240      # URLは切らない。本文は余裕を持たせる
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 60      # 公開直後は反映待ちでスキップ

RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

# ---------- 共通ユーティリティ ----------
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

# ---------- X 投稿 ----------
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

# ---------- リンク検出・正規化 ----------
def collect_text_blobs(entry) -> str:
    chunks = []
    for k in ("id","guid","link","title","summary"):
        v = entry.get(k)
        if isinstance(v,str): chunks.append(v)
    sd = entry.get("summary_detail") or {}
    if isinstance(sd,dict):
        v = sd.get("value")
        if isinstance(v,str): chunks.append(v)
    for c in entry.get("content", []):
        if isinstance(c,dict):
            v = c.get("value")
            if isinstance(v,str): chunks.append(v)
    for ln in entry.get("links", []):
        if isinstance(ln,dict):
            href = ln.get("href")
            if isinstance(href,str): chunks.append(href)
    return "\n".join(chunks)

def find_spotify_episode_url(entry) -> str | None:
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m:  return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2: return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None

def pick_mp3(entry) -> str | None:
    for enc in entry.get("enclosures", []):
        href = (enc.get("href") or "").strip()
        if href: return href
    return None

def normalize_link(link: str) -> str:
    try:
        link = (link or "").strip()
        if not link: return link
        if "open.spotify.com/episode/" in link:
            return link.split("?")[0]  # ?si=…等は削除
        return link
    except Exception:
        return link

def pick_best_link_for_podcast(entry, feed) -> str | None:
    # Spotify優先 → mp3 → 最後に entry.link の順。管理系URLは極力避ける
    sp = find_spotify_episode_url(entry)
    if sp: return normalize_link(sp)
    mp3 = pick_mp3(entry)
    if mp3: return normalize_link(mp3)
    link = (entry.get("link") or "").strip()
    if any(s in link for s in ["/play/","creators.spotify.com","podcasters.spotify.com"]):
        for ln in entry.get("links", []):
            href = (ln.get("href") or "").strip()
            if href and not any(s in href for s in ["/play/","creators.spotify.com","podcasters.spotify.com"]):
                return normalize_link(href)
    return normalize_link(link) if link else None

# ---------- テンプレ（日本語キー対応）＆文字数制御 ----------
def render_body_without_link(template: str, title: str, program: str) -> str:
    body = template
    for k in ("{title}","{タイトル}"):
        body = body.replace(k, title)
    for k in ("{program}","{番組名}"):
        body = body.replace(k, program)
    for k in ["{link}","{URL}","{Url}","{url}","{エピソードURL}"]:
        body = body.replace(k, "").rstrip()
    return body.replace("\r","").rstrip()

def compose_text(template: str, title: str, program: str, link: str, limit: int = MAX_TWEET_LEN) -> str:
    link = normalize_link(link)
    url_part = ("\n"+link) if link else ""
    body = render_body_without_link(template, title, program)
    candidate = (body + url_part).strip()
    if len(candidate) <= limit:
        return candidate
    for tag in [" #ReelPal"," #リルパル"," #Podcast"]:
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

# ---------- メイン ----------
def main():
    cfg   = json.load(open("feeds.json"))
    state = load_state()

    # 全 podcast フィードから候補を集め、最新1件だけ投稿
    candidates = []  # {ts, uid, text}

    for feed in cfg.get("feeds", []):
        if feed.get("type") != "podcast":
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
            link  = pick_best_link_for_podcast(entry, feed)
            if not link:
                continue  # 再生URLが未確定なら保留

            text = compose_text(tmpl, title, program, link, limit=MAX_TWEET_LEN)
            ts   = entry_timestamp(entry)
            candidates.append({"ts": ts, "uid": uid, "text": text})

    if not candidates:
        print("[INFO] no eligible podcast candidates this run")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    status, body = post_to_x(chosen["text"])
    if status < 300:
        state[chosen["uid"]] = int(time.time())
        save_state(state)
        print(f"[OK] posted podcast: {status}")
    else:
        print(f"[WARN] podcast post failed ({status}): {body}")

if __name__ == "__main__":
    main()
