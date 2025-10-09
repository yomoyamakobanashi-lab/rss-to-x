import os, json, time, hashlib, re, requests, feedparser, difflib
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state_podcast.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 60       # 公開直後は反映待ちでスキップ
ALLOW_MP3_FALLBACK = False  # ← mp3直リンクは使わない（Trueにすると最後にmp3を使う）

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

# ---------- タイトル正規化（Apple照合用） ----------
_PUNC = str.maketrans({c:"" for c in " \t\r\n\"'()[]{}.,!?！？。、・:：;；‐-–—―ー〜~…「」『』“”‘’／/\\|"})
def norm_title(s: str) -> str:
    if not s: return ""
    s = s.lower().translate(_PUNC)
    return s

def title_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=norm_title(a), b=norm_title(b)).ratio()

# ---------- Apple Podcasts 解決（collectionId 利用） ----------
def find_apple_episode_url(entry, collection_id: str | None, country="JP") -> str | None:
    if not collection_id:
        return None
    try:
        url = f"https://itunes.apple.com/lookup?id={collection_id}&entity=podcastEpisode&limit=200&country={country}"
        resp = requests.get(url, timeout=20)
        if resp.status_code >= 300:
            return None
        data = resp.json()
        results = [x for x in data.get("results", []) if x.get("wrapperType")=="podcastEpisode"]
        if not results: return None

        rss_title = (entry.get("title") or "").strip()
        rss_guid  = str(entry.get("id") or entry.get("guid") or "").strip()
        rss_ts    = entry_timestamp(entry)

        # 1) episodeGuid 完全一致
        if rss_guid:
            for it in results:
                if str(it.get("episodeGuid","")).strip() == rss_guid:
                    return it.get("trackViewUrl")

        # 2) タイトル完全一致（正規化後）
        for it in results:
            if norm_title(it.get("trackName","")) == norm_title(rss_title):
                return it.get("trackViewUrl")

        # 3) 類似度が高い（>=0.87）もの
        best = None; best_sim = 0.0
        for it in results:
            sim = title_sim(it.get("trackName",""), rss_title)
            if sim > best_sim:
                best_sim, best = sim, it
        if best and best_sim >= 0.87:
            return best.get("trackViewUrl")

        # 4) 公開日が近い（±3日）＋ 類似度中程度（>=0.65）を優先
        if rss_ts:
            near = []
            for it in results:
                try:
                    adt = datetime.fromisoformat(it.get("releaseDate","").replace("Z","+00:00"))
                    ats = int(adt.replace(tzinfo=timezone.utc).timestamp())
                    days = abs(ats - rss_ts)/86400.0
                except Exception:
                    continue
                if days <= 3:
                    sim = title_sim(it.get("trackName",""), rss_title)
                    near.append((sim, -abs(ats-rss_ts), it))
            if near:
                near.sort(reverse=True)
                if near[0][0] >= 0.65:
                    return near[0][2].get("trackViewUrl")

        return None
    except Exception:
        return None

# ---------- Spotify 解決 ----------
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
    # 1) Apple（collectionId があれば最優先）
    apple_id = feed.get("apple_collection_id")
    ap = find_apple_episode_url(entry, apple_id)
    if ap:
        return normalize_link(ap)

    # 2) Spotify
    sp = find_spotify_episode_url(entry)
    if sp:
        return normalize_link(sp)

    # 3) mp3（原則使わない）
    if ALLOW_MP3_FALLBACK:
        mp3 = pick_mp3(entry)
        if mp3:
            return normalize_link(mp3)

    # 見つからないなら今回は投稿しない（次回以降で再挑戦）
    return None

# ---------- 日本語キー対応テンプレ & 文字数制御（URLは切らない） ----------
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
                # Apple/Spotify がまだ出ていない回は保留（次回以降に再挑戦）
                print(f"[INFO] waiting for platform URL: {title}")
                continue

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
