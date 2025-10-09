import os, json, time, hashlib, requests, feedparser, re
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240      # URLを切らないため本文に余裕を持たせる
TITLE_MAXLEN   = 90       # タイトルの事前短縮目安（compose_text内でも段階短縮あり）
CHECK_ITEMS    = 8        # 最新から最大ここまで試す
FRESH_WAIT_MIN = 60       # 直後ポストは各プラットフォーム反映待ち

# Spotify 検出用
RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")

# ------------- 基本ユーティリティ -------------
def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w"))

def shorten_title(title, maxlen=TITLE_MAXLEN):
    t = (title or "").strip()
    return (t[:maxlen-1] + "…") if len(t) > maxlen else t

def minutes_since(entry) -> float:
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t:
        return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

def entries_newest_first(parsed):
    try:
        return sorted(
            parsed.entries,
            key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
            reverse=True
        )
    except Exception:
        return list(parsed.entries)

# ------------- X 投稿 -------------
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

# ------------- リンク検出・正規化 -------------
def collect_text_blobs(entry) -> str:
    """Spotify URL/URI を拾うため、考えられる全テキストを結合"""
    chunks = []
    for k in ("id", "guid", "link", "title", "summary"):
        v = entry.get(k)
        if isinstance(v, str):
            chunks.append(v)
    sd = entry.get("summary_detail") or {}
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str):
            chunks.append(v)
    for c in entry.get("content", []):
        if isinstance(c, dict):
            v = c.get("value")
            if isinstance(v, str):
                chunks.append(v)
    for ln in entry.get("links", []):
        if isinstance(ln, dict):
            href = ln.get("href")
            if isinstance(href, str):
                chunks.append(href)
    return "\n".join(chunks)

def find_spotify_episode_url(entry) -> str | None:
    """open.spotify.com/episode/<ID> を総当たりで検出。URI形式からの復元にも対応"""
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m:
        return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2:
        return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None

def find_apple_episode_url(entry, collection_id: str | None, country="JP") -> str | None:
    """
    Appleの Lookup API で番組ID(collectionId)からエピソード一覧を取り、
    RSS の id/guid や title と突き合わせて trackViewUrl を返す。
    """
    if not collection_id:
        return None
    try:
        url = f"https://itunes.apple.com/lookup?id={collection_id}&entity=podcastEpisode&limit=200&country={country}"
        resp = requests.get(url, timeout=20)
        if resp.status_code >= 300:
            return None
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        rss_title = (entry.get("title") or "").strip().lower()
        rss_guid  = str(entry.get("id") or entry.get("guid") or "").strip()

        # 1) episodeGuid 完全一致
        for it in results:
            if it.get("wrapperType") == "podcastEpisode":
                if rss_guid and str(it.get("episodeGuid","")).strip() == rss_guid:
                    return it.get("trackViewUrl")

        # 2) タイトル完全一致（大小無視）
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

def pick_mp3(entry) -> str | None:
    for enc in entry.get("enclosures", []):
        href = (enc.get("href") or "").strip()
        if href:
            return href
    return None

def normalize_link(link: str) -> str:
    """余計なクエリを外し、壊れにくい短いURLに正規化"""
    try:
        link = (link or "").strip()
        if not link:
            return link
        # Spotify: ?si=... などは削除して短く
        if "open.spotify.com/episode/" in link:
            return link.split("?")[0]
        return link
    except Exception:
        return link

def pick_best_link(entry, feed) -> str | None:
    """
    優先度：
      1) Apple（feeds.json に apple_collection_id がある場合）
      2) Spotify（全フィールド総当たり）
      3) enclosure（mp3）
      4) fallback: entry.link（/play/・creators/podcasters は避けたい）
    """
    # 1) Apple
    apple_id = feed.get("apple_collection_id")
    if apple_id:
        ap = find_apple_episode_url(entry, apple_id)
        if ap:
            return normalize_link(ap)

    # 2) Spotify
    sp = find_spotify_episode_url(entry)
    if sp:
        return normalize_link(sp)

    # 3) mp3
    mp3 = pick_mp3(entry)
    if mp3:
        return normalize_link(mp3)

    # 4) fallback（管理系URLは避けたいが、最後の手段）
    link = (entry.get("link") or "").strip()
    if any(s in link for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
        # links の他候補を探す
        for ln in entry.get("links", []):
            href = (ln.get("href") or "").strip()
            if href and not any(s in href for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
                return normalize_link(href)
    return normalize_link(link) if link else None

# ------------- 文字数制御（URLは絶対に切らない） -------------
def compose_text(template: str, title: str, program: str, link: str, limit: int = MAX_TWEET_LEN) -> str:
    """
    URLは必ず末尾に置き、URLは絶対に切らない。足りなければタイトルやタグ側を短縮。
    想定テンプレ: "🎧 新着エピソード公開！『{title}』｜{program} #Podcast #リルパル #ReelPal\n{link}"
    """
    link = normalize_link(link)
    url_part = ("\n" + link) if link else ""
    body = template.replace("{title}", title).replace("{program}", program).replace("{link}", "").rstrip()
    candidate = (body + url_part).strip()

    if len(candidate) <= limit:
        return candidate

    # 余計なタグを順に間引く
    for tag in [" #ReelPal", " #リルパル", " #Podcast", " #note"]:
        if len(candidate) <= limit:
            break
        body = body.replace(tag, "")
        candidate = (body + url_part).strip()

    if len(candidate) <= limit:
        return candidate

    # タイトルを段階的に短縮（URLは守る）
    for L in [90, 70, 50, 30, 15]:
        short_title = (title[:L-1] + "…") if len(title) > L else title
        body_short = template.replace("{title}", short_title).replace("{program}", program).replace("{link}", "").rstrip()
        candidate = (body_short + url_part).strip()
        if len(candidate) <= limit:
            return candidate

    # 最後の手：番組名＋URLのみ
    minimal = (program + url_part).strip() if link else program
    if len(minimal) <= limit:
        return minimal

    # さらに最後：URL単体
    return link

# ------------- メイン -------------
def main():
    cfg = json.load(open("feeds.json"))
    state = load_state()
    posted = False  # 今回1件でも成功したか

    for feed in cfg.get("feeds", []):
        if posted:
            break  # 1回の実行で最大1件のみ投稿（安定運用）

        url = feed["url"]
        tmpl = feed["template"]
        ftype = feed.get("type", "")
        program = feed.get("program_name", "")

        parsed = feedparser.parse(url)

        # 最新→古い の順で最大 CHECK_ITEMS 件だけ試す
        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue  # 既に投稿済み

            # 直後は各ディレクトリ取り込み待ち
            age_min = minutes_since(entry)
            if age_min < FRESH_WAIT_MIN:
                print(f"[INFO] too fresh ({age_min:.0f}m) → skip for now: {(entry.get('title') or '').strip()}")
                continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)

            # リンク生成（podcastはApple/Spotify優先。note等はそのまま）
            if ftype == "podcast":
                best_link = pick_best_link(entry, feed)
                if not best_link:
                    print(f"[INFO] playable link not found yet. Will retry later: {title}")
                    continue
            else:
                best_link = (entry.get("link") or "").strip()

            # URLは末尾固定・URLは絶対に切らない本文生成
            text = compose_text(tmpl, title, program, best_link, limit=MAX_TWEET_LEN)

            status, body = post_to_x(text)
            if status < 300:
                state[uid] = int(time.time())
                save_state(state)
                print(f"[OK] posted: {title} ({status}) -> {best_link}")
                posted = True
                break
            else:
                print(f"[WARN] post failed ({status}): {body}")
                # 失敗は state に記録しない＝次回も再挑戦できる

    if not posted:
        print("[INFO] no new items posted this run")

if __name__ == "__main__":
    main()
