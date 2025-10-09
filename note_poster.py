import os, json, time, hashlib, requests, feedparser, re
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

STATE_FILE = "state_note.json"

MAX_TWEET_LEN  = 240
TITLE_MAXLEN   = 90
CHECK_ITEMS    = 8
FRESH_WAIT_MIN = 10   # noteは反映が速い
TCO_URL_LEN    = 23   # URLは常に23文字換算

RE_URL_ANY = re.compile(r"https?://[^\s\)\]\}<>]+")

# ================= 基本ユーティリティ =================
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

# ================= X API =================
def get_oauth():
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")
    return OAuth1(api_key, api_secret, access_token, access_secret)

def post_to_x(text: str, media_id: str | None = None):
    auth = get_oauth()
    payload = {"text": text}
    if media_id:
        payload["media"] = {"media_ids": [media_id]}
    r = requests.post("https://api.x.com/2/tweets",
                      auth=auth,
                      json=payload,
                      headers={"Content-Type": "application/json"},
                      timeout=20)
    return r.status_code, r.text

def upload_media(img_bytes: bytes):
    """
    v1.1 media upload（画像を添付用にアップロード）
    返り値: media_id_string or None
    """
    auth = get_oauth()
    files = {"media": img_bytes}
    try:
        r = requests.post("https://upload.x.com/1.1/media/upload.json",
                          auth=auth,
                          files=files,
                          timeout=30)
        if r.status_code >= 300:
            print(f"[WARN] media upload failed: {r.status_code} {r.text}")
            return None
        return r.json().get("media_id_string")
    except Exception as e:
        print(f"[WARN] media upload exception: {e}")
        return None

# ================= テキスト整形（URLは切らない） =================
def render_body_without_link(template: str, title: str, program: str) -> str:
    body = template
    for k in ("{title}", "{タイトル}"):
        body = body.replace(k, title)
    for k in ("{program}", "{番組名}"):
        body = body.replace(k, program)
    # リンク用プレースホルダは空に
    for k in ["{link}", "{URL}", "{Url}", "{url}", "{記事URL}"]:
        body = body.replace(k, "").rstrip()
    return body.replace("\r", "").rstrip()

def extract_prefix(template: str) -> str:
    keys = ["{title}","{タイトル}","{program}","{番組名}","{link}","{URL}","{Url}","{url}","{記事URL}"]
    idxs = [template.find(k) for k in keys if k in template]
    cut = min([i for i in idxs if i >= 0], default=len(template))
    return template[:cut].strip()

def weighted_len_no_urls(s: str) -> int:
    # ASCII=1 / 非ASCII=2 で概算
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in s)

def x_length(s: str) -> int:
    total = 0
    last = 0
    for m in RE_URL_ANY.finditer(s):
        seg = s[last:m.start()]
        total += weighted_len_no_urls(seg)
        total += TCO_URL_LEN
        last = m.end()
    total += weighted_len_no_urls(s[last:])
    return total

def compose_text(template: str, title: str, program: str, link: str, limit: int = 280) -> str:
    """
    定型文・タグは必ず残す。URLは末尾＆23文字換算。残りでタイトルだけ短縮。
    """
    url_part = ("\n" + link) if link else ""
    prefix = extract_prefix(template)

    body = render_body_without_link(template, title, program)
    candidate = (body + url_part).strip()
    if x_length(candidate) <= limit:
        return candidate

    # タグ間引きは禁止→“必ず残す”ので、タイトルのみ段階短縮
    for L in [90, 70, 50, 30, 15]:
        short_title = (title[:L-1] + "…") if len(title) > L else title
        body_short = render_body_without_link(template, short_title, program)
        candidate = (body_short + url_part).strip()
        if x_length(candidate) <= limit:
            return candidate

    # それでも超えるレアケース：定型文＋番組名＋URL
    minimal = ((prefix + " " + program).strip() + url_part) if link else (prefix + " " + program).strip()
    if x_length(minimal) <= limit and minimal.strip():
        return minimal

    # さらに最後：定型文＋URL
    prefix_only = (prefix + url_part).strip()
    return prefix_only if x_length(prefix_only) <= limit else link

# ================= noteのOG画像取得 =================
def fetch_note_og_image(url: str) -> bytes | None:
    """
    note記事の <meta property="og:image"> / <meta name="twitter:image"> を拾って画像を取得。
    失敗時は None を返す。
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=15).text
        # og:image 優先
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            return None
        img_url = m.group(1)
        # 画像取得
        r = requests.get(img_url, headers=headers, timeout=20)
        if r.status_code >= 300:
            return None
        # サイズが大きすぎる場合は諦める（再エンコード無し運用）
        if int(r.headers.get("Content-Length", "0")) > 5 * 1024 * 1024:
            return None
        return r.content
    except Exception as e:
        print(f"[WARN] fetch og:image failed: {e}")
        return None

# ================= メイン =================
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

            text = compose_text(tmpl, title, program, link, limit=280)

            # ---- ここでOG画像を取得 → メディアとして添付 ----
            media_id = None
            img_bytes = fetch_note_og_image(link)
            if img_bytes:
                media_id = upload_media(img_bytes)

            # 投稿
            status, body = post_to_x(text, media_id=media_id)
            if status < 300:
                state[uid] = int(time.time())
                save_state(state)
                print(f"[OK] posted note: {status} media={'yes' if media_id else 'no'}")
                return
            else:
                print(f"[WARN] note post failed ({status}): {body}")
                # 失敗は state に記録しない→次回再挑戦

    print("[INFO] no eligible note candidates this run")

if __name__ == "__main__":
    main()
