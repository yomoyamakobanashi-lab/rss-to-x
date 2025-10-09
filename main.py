import os, json, time, hashlib, requests, feedparser, re
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state.json"

# ===== 運用パラメータ =====
MAX_TWEET_LEN = 240      # URLを切らないため本文に余裕を持たせる
TITLE_MAXLEN   = 90       # 事前のタイトル短縮目安
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
      1) Spotify（全フィールド総当たりで再生URL検出）
      2) enclosure（mp3）
      3) fallback: entry.link（/play/・creators/podcasters は最後の手段）
    ※ Apple優先にしたい場合はここに find_apple_episode_url を組み込んでください
    """
    sp = find_spotify_episode_url(entry)
    if sp:
        return normalize_link(sp)

    mp3 = pick_mp3(entry)
    if mp3:
        return normalize_link(mp3)

    link = (entry.get("link") or "").strip()
    if any(s in link for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
        for ln in entry.get("links", []):
            href = (ln.get("href") or "").strip()
            if href and not any(s in href for s in ["/play/", "creators.spotify.com", "podcasters.spotify.com"]):
                return normalize_link(href)
    return normalize_link(link) if link else None

# ------------- テンプレ置換（日本語キー対応） -------------
def render_body_without_link(template: str, title: str, program: str, feed_type: str) -> str:
    """
    {title}/{program}/{link} だけでなく、
    {タイトル}/{番組名}/{エピソードURL}/{記事URL} もサポート。
    ここではリンク系プレースホルダは空にし、本文だけ作る。
    """
    body = template

    # タイトル置換（英/日）
    for k in ("{title}", "{タイトル}"):
        body = body.replace(k, title)

    # 番組名置換（英/日）
    for k in ("{program}", "{番組名}"):
        body = body.replace(k, program)

    # リンク系は空に（後で末尾にURLを付ける）
    link_keys = ["{link}", "{URL}", "{Url}", "{url}"]
    if feed_type == "podcast":
        link_keys += ["{エピソードURL}"]
    else:
        link_keys += ["{記事URL}"]

    for k in link_keys:
        body = body.replace(k, "").rstrip()

    # 余計な空白・改行を軽く整形
    body = body.replace("\r", "").rstrip()
    return body

# ------------- 文字数制御（URLは絶対に切らない） -------------
def compose_text(template: str, title: str, program: str, link: str, feed_type: str, limit: int = MAX_TWEET_LEN) -> str:
    """
    URLは必ず末尾に置き、URLは絶対に切らない。足りなければタイトルやタグ側を短縮。
    想定テンプレ例:
      Podcast: "🎧 新着エピソード公開！『{title}』｜{program} #Podcast #リルパル #ReelPal\n{link}"
      Note   : "📝 新着note『{title}』 #note #リルパル #ReelPal\n{link}"
    """
    link = normalize_link(link)
    url_part = ("\n" + link) if link else ""

    # 1) まず本文（リンクなし）を作る
    body = render_body_without_link(template, title, program, feed_type)
    candidate = (body + url_part).strip()
    if len(candidate) <= limit:
        return candidate

    # 2) タグを間引く（順に消す）
    for tag in [" #ReelPal", " #リルパル", " #Podcast", " #note"]:
        if len(candidate) <= limit:
            break
        body = body.replace(tag, "")
        candidate = (body + url_part).strip()
    if len(candidate) <= limit:
        return candidate

    # 3) タイトルを段階的に短縮（URLは守る）
    for L in [90, 70, 50, 30, 15]:
        short_title = (title[:L-1] + "…") if len(title) > L else title
        body_short = render_body_without_link(template, short_title, program, feed_type)
        candidate = (body_short + url_part).strip()
        if len(candidate) <= limit:
            return candidate

    # 4) 最後の手：番組名＋URLのみ
    minimal = (program + url_part).strip() if link else program
    if len(minimal) <= limit:
        return minimal

    # 5) さらに最後：URL単体
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
        ftype = feed.get("type", "")  # "podcast" or "note" を想定
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

            # リンク生成
            if ftype == "podcast":
                best_link = pick_best_link(entry, feed)
                if not best_link:
                    print(f"[INFO] playable link not found yet. Will retry later: {title}")
                    continue
            else:
                best_link = (entry.get("link") or "").strip()

            # URLは末尾固定・URLは絶対に切らない本文生成（日本語キー対応）
            text = compose_text(tmpl, title, program, best_link, feed_type=ftype, limit=MAX_TWEET_LEN)

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
