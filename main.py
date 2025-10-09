import os, json, time, hashlib, requests, feedparser, re
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 60       # 直後は各プラットフォームの反映待ち

RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s): json.dump(s, open(STATE_FILE, "w"))

def short_safe(text, n=MAX_TWEET_LEN):
    return (text[:n-1] + "…") if len(text) > n else text

def shorten_title(title, maxlen=TITLE_MAXLEN):
    t = (title or "").strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def post_to_x(text):
    api_key = os.getenv("X_API_KEY"); api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN"); access_secret = os.getenv("X_ACCESS_SECRET")
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

def entries_newest_first(parsed):
    try:
        return sorted(parsed.entries,
                      key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
                      reverse=True)
    except Exception:
        return list(parsed.entries)

def minutes_since(entry):
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t: return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

# ---- Apple Podcasts 解決（collectionId 利用） ----
def find_apple_episode_url(entry, collection_id, country="JP"):
    """
    Appleの Lookup API で番組ID(collectionId)からエピソード一覧を取得し、
    タイトル or GUID で突き合わせて trackViewUrl を返す。
    """
    if not collection_id: 
        return None
    try:
        # 番組に紐づく最新エピソード群を取得（多めに）
        url = f"https://itunes.apple.com/lookup?id={collection_id}&entity=podcastEpisode&limit=200&country={country}"
        resp = requests.get(url, timeout=20)
        if resp.status_code >= 300:
            return None
        data = resp.json()
        results = data.get("results", [])
        if not results: 
            return None

        # RSS側の手がかり
        rss_title = (entry.get("title") or "").strip().lower()
        rss_guid  = str(entry.get("id") or entry.get("guid") or "").strip()

        # 1) episodeGuid 一致（最優先・安定）
        for it in results:
            if it.get("wrapperType") == "podcastEpisode":
                if rss_guid and str(it.get("episodeGuid","")).strip() == rss_guid:
                    return it.get("trackViewUrl")

        # 2) タイトル一致（前後空白・大文字小文字無視）
        for it in results:
            if it.get("wrapperType") == "podcastEpisode":
                name = (it.get("trackName") or "").strip().lower()
                if name and rss_title and name == rss_title:
                    return it.get("trackViewUrl")

        # 3) タイトル部分一致（保険）
        for it in results:
            if it.get("wrapperType") == "podcastEpisode":
                name = (it.get("trackName") or "").strip().lower()
                if name and rss_title and (rss_title in name or name in rss_title):
                    return it.get("trackViewUrl")

        return None
    except Exception:
        return None

# ---- Spotify 解決（総当たり） ----
def collect_text_blobs(entry):
    chunks = []
    for k in ("id","guid","link","title","summary"):
        v = entry.get(k); 
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

def find_spotify_episode_url(entry):
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m: return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2: return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None

def pick_mp3(entry):
    for enc in entry.get("enclosures", []):
        href = (enc.get("href") or "").strip()
        if href: return href
    return (entry.get("link") or "").strip()

def main():
    cfg   = json.load(open("feeds.json"))
    state = load_state()
    posted = False

    for feed in cfg.get("feeds", []):
        if posted: break

        url   = feed["url"]
        tmpl  = feed["template"]
        ftype = feed.get("type","")
        program = feed.get("program_name","")
        apple_collection_id = feed.get("apple_collection_id")  # ← 追加

        parsed = feedparser.parse(url)

        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state: 
                continue

            if minutes_since(entry) < FRESH_WAIT_MIN:
                print(f"[INFO] too fresh → skip for now: {(entry.get('title') or '').strip()}")
                continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)

            link = ""
            if ftype == "podcast":
                # ① Apple優先（collectionId があれば極めて安定）
                link = find_apple_episode_url(entry, apple_collection_id) or ""
                # ② だめなら Spotify
                if not link:
                    link = find_spotify_episode_url(entry) or ""
                # ③ それでも無ければ mp3
                if not link:
                    link = pick_mp3(entry)
            else:
                # note 等
                link = (entry.get("link") or "").strip()

            if not link:
                print(f"[INFO] playable link not found yet. Will retry later: {title}")
                continue

            text = short_safe(tmpl.format(title=title, link=link, program=program), MAX_TWEET_LEN)
            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time()); save_state(state)
                print(f"[OK] posted: {title} ({status}) -> {link}")
                posted = True
                break
            else:
                print(f"[WARN] post failed ({status}): {body}")

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
