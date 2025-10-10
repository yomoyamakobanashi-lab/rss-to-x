#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Podcast → X 自動投稿（Apple → Spotify 優先 / mp3直リンクは原則回避）
- v2 POST /2/tweets を OAuth1 ユーザーコンテキストで叩く
- 成功は HTTP 200/201 かつ {"data":{"id": ...}} を厳密判定
- 失敗時は非ゼロ終了（Actionsが赤くなる）
"""

import os
import json
import time
import hashlib
import re
import difflib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import requests
import feedparser
from requests_oauthlib import OAuth1
import sys

# ========= 設定 =========
STATE_FILE = "state_podcast.json"

# 運用パラメータ
MAX_TWEET_LIMIT = 280
TCO_URL_LEN     = 23
TITLE_MAXLEN    = 200
CHECK_ITEMS     = 8
FRESH_WAIT_MIN  = 60
ALLOW_MP3_FALLBACK = False

HTTP_TIMEOUT = 20
HTTP_RETRY   = 2
USER_AGENT   = "rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)"

# ログ
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("podcast_poster")

# 環境
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# ========= 正規表現 =========
RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")
RE_URL_ANY     = re.compile(r"https?://[^\s\)\]\}<>]+")

_PUNC = str.maketrans({c: "" for c in " \t\r\n\"'()[]{}.,!?！？。、・:：;；‐-–—―ー〜~…「」『』“”‘’／/\\|"})

# ========= HTTP ラッパ =========
def _requests_get(url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    last_exc = None
    for i in range(HTTP_RETRY + 1):
        try:
            return requests.get(url, headers=headers, timeout=HTTP_TIMEOUT, **kwargs)
        except Exception as e:
            last_exc = e
            logger.warning("GET failed (%s) try=%d/%d", url, i + 1, HTTP_RETRY + 1)
            time.sleep(1.0 + i * 1.5)
    raise last_exc  # type: ignore

# ========= state =========
def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("state読み込み失敗: %s（空で続行）", e)
        return {}

def save_state(s: Dict[str, Any]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

# ========= ユーティリティ =========
def shorten_title(title: str, maxlen: int = TITLE_MAXLEN) -> str:
    t = (title or "").strip()
    return t if len(t) <= maxlen else (t[: maxlen - 1] + "…")

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

def entries_newest_first(parsed) -> List[Any]:
    try:
        return sorted(parsed.entries,
                      key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
                      reverse=True)
    except Exception:
        return list(parsed.entries)

# ========= タイトル正規化 =========
def norm_title(s: str) -> str:
    if not s: return ""
    return s.lower().translate(_PUNC)

def title_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=norm_title(a), b=norm_title(b)).ratio()

# ========= Apple Podcasts 解決 =========
def find_apple_episode_url(entry, collection_id: Optional[str], country: str = "JP") -> Optional[str]:
    if not collection_id:
        return None
    try:
        url = ( "https://itunes.apple.com/lookup"
                f"?id={collection_id}&entity=podcastEpisode&limit=200&country={country}" )
        resp = _requests_get(url)
        if resp.status_code >= 300:
            logger.info("Apple lookup失敗: %s %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        results = [x for x in data.get("results", []) if x.get("wrapperType") == "podcastEpisode"]
        if not results:
            return None

        rss_title = (getattr(entry, "title", "") or "").strip()
        rss_guid  = str(getattr(entry, "id", "") or getattr(entry, "guid", "") or "").strip()
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

        # 3) 類似度高（>=0.87）
        best, best_sim = None, 0.0
        for it in results:
            sim = title_sim(it.get("trackName",""), rss_title)
            if sim > best_sim:
                best_sim, best = sim, it
        if best and best_sim >= 0.87:
            return best.get("trackViewUrl")

        # 4) 日付近い（±3日）かつ 類似度中（>=0.65）
        if rss_ts:
            near: List[Tuple[float,int,dict]] = []
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
    except Exception as e:
        logger.warning("Apple解決で例外: %s", e)
        return None

# ========= Spotify 解決 =========
def collect_text_blobs(entry) -> str:
    chunks: List[str] = []
    for k in ("id","guid","link","title","summary"):
        v = getattr(entry, k, None)
        if isinstance(v, str): chunks.append(v)
    sd = getattr(entry, "summary_detail", None) or {}
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str): chunks.append(v)
    contents = getattr(entry, "content", []) or []
    for c in contents:
        if isinstance(c, dict):
            v = c.get("value")
            if isinstance(v, str): chunks.append(v)
    links = getattr(entry, "links", []) or []
    for ln in links:
        if isinstance(ln, dict):
            href = ln.get("href")
            if isinstance(href, str): chunks.append(href)
    return "\n".join(chunks)

def find_spotify_episode_url(entry) -> Optional[str]:
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m:  return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2: return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None

def normalize_link(link: str) -> str:
    try:
        link = (link or "").strip()
        if not link: return link
        if "open.spotify.com/episode/" in link:
            return link.split("?")[0]
        return link
    except Exception:
        return link

def pick_best_link_for_podcast(entry, feed: Dict[str, Any]) -> Optional[str]:
    apple_id = feed.get("apple_collection_id")
    ap = find_apple_episode_url(entry, apple_id)
    if ap: return normalize_link(ap)
    sp = find_spotify_episode_url(entry)
    if sp: return normalize_link(sp)
    if ALLOW_MP3_FALLBACK:
        enclosures = getattr(entry, "enclosures", []) or []
        for enc in enclosures:
            href = (enc.get("href") or "").strip()
            if href: return normalize_link(href)
    return None

# ========= テンプレ処理 =========
def render_body_without_link(template: str, title: str, program: str) -> str:
    body = template
    for k in ("{title}","{タイトル}"):
        body = body.replace(k, title)
    for k in ("{program}","{番組名}"):
        body = body.replace(k, program)
    for k in ["{link}","{URL}","{Url}","{url}","{エピソードURL}"]:
        body = body.replace(k, "").rstrip()
    return body.replace("\r","").rstrip()

def extract_prefix(template: str) -> str:
    keys = ["{title}","{タイトル}","{program}","{番組名}","{link}","{URL}","{Url}","{url}","{エピソードURL}"]
    idxs = [template.find(k) for k in keys if k in template]
    cut = min([i for i in idxs if i >= 0], default=len(template))
    return template[:cut].strip()

def weighted_len_no_urls(s: str) -> int:
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in s)

def x_length(s: str) -> int:
    total = 0; last = 0
    for m in RE_URL_ANY.finditer(s):
        seg = s[last:m.start()]
        total += weighted_len_no_urls(seg)
        total += TCO_URL_LEN
        last = m.end()
    total += weighted_len_no_urls(s[last:])
    return total

def smart_truncate(title: str, keep: int) -> str:
    if keep >= len(title): return title
    if keep <= 0: return ""
    return title[:keep-1] + "…"

def compose_with_title(template: str, title: str, program: str, link: str) -> str:
    body = render_body_without_link(template, title, program)
    link = normalize_link(link)
    return (body + ("\n" + link if link else "")).strip()

def compose_text(template: str, title: str, program: str, link: str,
                 limit: int = MAX_TWEET_LIMIT) -> str:
    pref = extract_prefix(template)
    cand = compose_with_title(template, title, program, link)
    if x_length(cand) <= limit:
        return cand
    lo, hi = 0, len(title); best = None
    while lo <= hi:
        mid = (lo + hi)//2
        cand_mid = compose_with_title(template, smart_truncate(title, mid), program, link)
        if x_length(cand_mid) <= limit:
            best = cand_mid; lo = mid + 1
        else:
            hi = mid - 1
    if best: return best
    cand_progless = compose_with_title(template, "", "", link)
    if x_length(cand_progless) <= limit and pref:
        return cand_progless
    cand_prefix = (pref + ("\n" + normalize_link(link) if link else "")).strip()
    if x_length(cand_prefix) <= limit and pref:
        return cand_prefix
    return normalize_link(link)

# ========= X API =========
def post_to_x(text: str) -> Tuple[int, str]:
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")

    if DRY_RUN:
        logger.info("[DRY_RUN] Tweet would be:\n%s", text)
        return 200, '{"dry_run": true}'

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    url = "https://api.twitter.com/2/tweets"
    try:
        r = requests.post(
            url,
            auth=auth,
            json={"text": text},
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        logger.info("X response status=%s body=%s", r.status_code, (r.text or "")[:800])
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code in (200, 201) and isinstance(j, dict) and "data" in j and "id" in j["data"]:
            return r.status_code, r.text
        return r.status_code if r.status_code else 599, r.text
    except Exception as e:
        return 599, f"exception: {e}"

# （任意）トークンが指すユーザー確認（必要時のみ呼び出し）
def whoami() -> Optional[str]:
    try:
        api_key = os.getenv("X_API_KEY")
        api_secret = os.getenv("X_API_SECRET")
        access_token = os.getenv("X_ACCESS_TOKEN")
        access_secret = os.getenv("X_ACCESS_SECRET")
        auth = OAuth1(api_key, api_secret, access_token, access_secret)
        r = requests.get("https://api.twitter.com/2/users/me",
                         auth=auth,
                         headers={"User-Agent": USER_AGENT},
                         timeout=HTTP_TIMEOUT)
        if r.status_code >= 300:
            logger.info("whoami failed: %s %s", r.status_code, r.text[:200])
            return None
        u = r.json().get("data", {})
        logger.info("Posting as: @%s (id=%s)", u.get("username"), u.get("id"))
        return u.get("username")
    except Exception as e:
        logger.info("whoami exception: %s", e)
        return None

# ========= メイン =========
def main() -> None:
    # feeds.json
    try:
        with open("feeds.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        logger.error("feeds.json 読み込み失敗: %s", e)
        sys.exit(2)

    whoami()  # 任意ログ

    state = load_state()
    candidates: List[Dict[str, Any]] = []

    feeds = cfg.get("feeds", [])
    if not isinstance(feeds, list):
        logger.error("feeds.json の形式が不正（feeds が list ではない）")
        sys.exit(2)

    for feed in feeds:
        if not isinstance(feed, dict): continue
        if feed.get("type") != "podcast": continue

        url   = feed.get("url")
        tmpl  = feed.get("template")
        program = feed.get("program_name", "")
        if not url or not tmpl:
            logger.warning("feed定義不足: %s", feed); continue

        logger.info("Fetching RSS: %s", url)
        parsed = feedparser.parse(url)
        if parsed.bozo:
            logger.warning("RSS parse 警告: %s", getattr(parsed, "bozo_exception", None))

        entries = entries_newest_first(parsed)
        if not entries:
            logger.info("RSSにエントリなし: %s", url); continue

        for entry in entries[:CHECK_ITEMS]:
            uid_src = (getattr(entry, "id", None) or getattr(entry, "guid", None)
                       or getattr(entry, "link", None) or getattr(entry, "title", None))
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state: 
                continue
            if minutes_since(entry) < FRESH_WAIT_MIN:
                logger.info("[WAIT] fresh episode（%s）", getattr(entry, "title", ""))
                continue

            link = pick_best_link_for_podcast(entry, feed)
            if not link:
                logger.info("[INFO] Apple/Spotify URL未解決: %s", (getattr(entry, "title", "") or "").strip())
                continue

            title = shorten_title(getattr(entry, "title", "") or "", maxlen=TITLE_MAXLEN)
            text  = compose_text(tmpl, title, program, link, limit=MAX_TWEET_LIMIT)
            ts    = entry_timestamp(entry)
            candidates.append({"ts": ts, "uid": uid, "text": text})

    if not candidates:
        logger.info("[INFO] 投稿候補なし（今回の実行）")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    logger.info("Posting text:\n%s", chosen["text"])

    status, body = post_to_x(chosen["text"])
    if status in (200,201):
        try:
            j = json.loads(body)
            tid = j.get("data", {}).get("id")
        except Exception:
            tid = None
        if tid:
            state[chosen["uid"]] = int(time.time())
            save_state(state)
            logger.info("[OK] posted tweet id=%s", tid)
            return
        else:
            logger.error("[NG] No tweet id in response: %s", body[:400])
            sys.exit(3)
    else:
        logger.error("[NG] X post failed status=%s body=%s", status, (body or "")[:400])
        sys.exit(4)

if __name__ == "__main__":
    main()
