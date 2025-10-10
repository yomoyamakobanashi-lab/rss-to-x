#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Podcast → X 自動投稿スクリプト（Apple → Spotify 優先、mp3直リンクは原則回避）
- 文字数は URL=常に23 として算出、テンプレの固定句・タグは維持、タイトルのみを段階的に縮約
- 新着検出は state_podcast.json（エントリ単位のUID）で管理
- 公開直後はプラットフォーム反映待ち（FRESH_WAIT_MIN 分）を経てから投稿候補化
- Appleは collectionId を feeds.json 側に設定しておくと高精度に解決
- X への投稿は OAuth1（ユーザーコンテキスト）。Secrets 必須：X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET
"""

import os
import json
import time
import hashlib
import re
import difflib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests
import feedparser
from requests_oauthlib import OAuth1

# ========= 設定 =========
STATE_FILE = "state_podcast.json"

# 運用パラメータ
MAX_TWEET_LIMIT = 280          # Xの文字上限
TCO_URL_LEN     = 23           # URLは常に23文字として計算
TITLE_MAXLEN    = 200          # 生タイトルの上限（最終的には縮約）
CHECK_ITEMS     = 8            # RSSの先頭から何件見るか
FRESH_WAIT_MIN  = 60           # 公開直後の反映待ち（分）
ALLOW_MP3_FALLBACK = False     # mp3直リンクは基本使わない

HTTP_TIMEOUT = 20              # 外部APIのタイムアウト
HTTP_RETRY   = 2               # 軽いリトライ回数
USER_AGENT   = "rss-to-x/1.0 (+https://github.com/your/repo)"

# ロギング
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("podcast_poster")

# 環境オプション
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"   # 1なら投稿せずログ出力のみ

# ========= 正規表現 =========
RE_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/episode/([A-Za-z0-9]+)")
RE_SPOTIFY_URI = re.compile(r"spotify:episode:([A-Za-z0-9]+)")
RE_URL_ANY     = re.compile(r"https?://[^\s\)\]\}<>]+")

# Appleタイトル正規化用マップ
_PUNC = str.maketrans({c: "" for c in " \t\r\n\"'()[]{}.,!?！？。、・:：;；‐-–—―ー〜~…「」『』“”‘’／/\\|"})


# ========= ユーティリティ =========
def _requests_get(url: str, **kwargs) -> requests.Response:
    """requests.get をUA・タイムアウト・簡易リトライ付きでラップ"""
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)

    last_exc = None
    for i in range(HTTP_RETRY + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT, **kwargs)
            return resp
        except Exception as e:
            last_exc = e
            logger.warning("GET failed (%s) try=%d/%d", url, i + 1, HTTP_RETRY + 1)
            time.sleep(1.0 + i * 1.5)
    raise last_exc  # type: ignore


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("state読み込み失敗: %s（破損の可能性、空で継続）", e)
        return {}


def save_state(s: Dict[str, Any]) -> None:
    # 原子的セーブ
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def shorten_title(title: str, maxlen: int = TITLE_MAXLEN) -> str:
    t = (title or "").strip()
    return t if len(t) <= maxlen else (t[: maxlen - 1] + "…")


def entry_timestamp(entry) -> int:
    """feedparser entry → epoch（秒）。取得不可なら 0"""
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    try:
        return int(time.mktime(t)) if t else 0
    except Exception:
        return 0


def minutes_since(entry) -> float:
    """現在(UTC)からの経過分。published/updated が無ければ巨大値"""
    t = getattr(entry, "published_parsed", getattr(entry, "updated_parsed", None))
    if not t:
        return 1e9
    dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def entries_newest_first(parsed) -> List[Any]:
    try:
        return sorted(
            parsed.entries,
            key=lambda x: getattr(x, "published_parsed", getattr(x, "updated_parsed", None)) or 0,
            reverse=True,
        )
    except Exception:
        return list(parsed.entries)


# ========= X 投稿 =========
def post_to_x(text: str) -> (int, str):
    """X v2 POST /2/tweets（OAuth1 ユーザーコンテキスト）"""
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Xのキーが未設定です（Secrets: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）")

    if DRY_RUN:
        logger.info("[DRY_RUN] Tweet would be:\n%s", text)
        return 200, '{"dry_run": true}'

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    url = "https://api.twitter.com/2/tweets"  # api.x.com でも可だが公式は twitter.com
    try:
        r = requests.post(
            url,
            auth=auth,
            json={"text": text},
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        return r.status_code, r.text
    except Exception as e:
        return 599, f"exception: {e}"


# ========= タイトル正規化（Apple照合用） =========
def norm_title(s: str) -> str:
    if not s:
        return ""
    return s.lower().translate(_PUNC)


def title_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=norm_title(a), b=norm_title(b)).ratio()


# ========= Apple Podcasts 解決 =========
def find_apple_episode_url(entry, collection_id: Optional[str], country: str = "JP") -> Optional[str]:
    if not collection_id:
        return None
    try:
        url = (
            "https://itunes.apple.com/lookup"
            f"?id={collection_id}&entity=podcastEpisode&limit=200&country={country}"
        )
        resp = _requests_get(url)
        if resp.status_code >= 300:
            logger.info("Apple lookup失敗: %s %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        results = [x for x in data.get("results", []) if x.get("wrapperType") == "podcastEpisode"]
        if not results:
            return None

        rss_title = (getattr(entry, "title", "") or "").strip()
        rss_guid = str(getattr(entry, "id", "") or getattr(entry, "guid", "") or "").strip()
        rss_ts = entry_timestamp(entry)

        # 1) episodeGuid 完全一致
        if rss_guid:
            for it in results:
                if str(it.get("episodeGuid", "")).strip() == rss_guid:
                    return it.get("trackViewUrl")

        # 2) タイトル完全一致（正規化後）
        for it in results:
            if norm_title(it.get("trackName", "")) == norm_title(rss_title):
                return it.get("trackViewUrl")

        # 3) 類似度が高い（>=0.87）
        best = None
        best_sim = 0.0
        for it in results:
            sim = title_sim(it.get("trackName", ""), rss_title)
            if sim > best_sim:
                best_sim, best = sim, it
        if best and best_sim >= 0.87:
            return best.get("trackViewUrl")

        # 4) 公開日が近い（±3日）＋ 類似度中程度（>=0.65）
        if rss_ts:
            near = []
            for it in results:
                try:
                    adt = datetime.fromisoformat(it.get("releaseDate", "").replace("Z", "+00:00"))
                    ats = int(adt.replace(tzinfo=timezone.utc).timestamp())
                    days = abs(ats - rss_ts) / 86400.0
                except Exception:
                    continue
                if days <= 3:
                    sim = title_sim(it.get("trackName", ""), rss_title)
                    near.append((sim, -abs(ats - rss_ts), it))
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
    for k in ("id", "guid", "link", "title", "summary"):
        v = getattr(entry, k, None)
        if isinstance(v, str):
            chunks.append(v)

    sd = getattr(entry, "summary_detail", None) or {}
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str):
            chunks.append(v)

    contents = getattr(entry, "content", []) or []
    for c in contents:
        if isinstance(c, dict):
            v = c.get("value")
            if isinstance(v, str):
                chunks.append(v)

    links = getattr(entry, "links", []) or []
    for ln in links:
        if isinstance(ln, dict):
            href = ln.get("href")
            if isinstance(href, str):
                chunks.append(href)

    return "\n".join(chunks)


def find_spotify_episode_url(entry) -> Optional[str]:
    blob = collect_text_blobs(entry)
    m = RE_SPOTIFY_URL.search(blob)
    if m:
        return f"https://open.spotify.com/episode/{m.group(1)}"
    m2 = RE_SPOTIFY_URI.search(blob)
    if m2:
        return f"https://open.spotify.com/episode/{m2.group(1)}"
    return None


def normalize_link(link: str) -> str:
    try:
        link = (link or "").strip()
        if not link:
            return link
        if "open.spotify.com/episode/" in link:
            return link.split("?")[0]  # ?si=… 等は除去
        return link
    except Exception:
        return link


def pick_best_link_for_podcast(entry, feed: Dict[str, Any]) -> Optional[str]:
    # 1) Apple（collectionId があれば最優先）
    apple_id = feed.get("apple_collection_id")
    ap = find_apple_episode_url(entry, apple_id)
    if ap:
        return normalize_link(ap)

    # 2) Spotify
    sp = find_spotify_episode_url(entry)
    if sp:
        return normalize_link(sp)

    # 3) mp3は使わない（必要なら ALLOW_MP3_FALLBACK=True）
    if ALLOW_MP3_FALLBACK:
        enclosures = getattr(entry, "enclosures", []) or []
        for enc in enclosures:
            href = (enc.get("href") or "").strip()
            if href:
                return normalize_link(href)

    # 見つからなければ今回は投稿しない（次回以降再挑戦）
    return None


# ========= テンプレート処理 =========
def render_body_without_link(template: str, title: str, program: str) -> str:
    """{title}/{program} 等を置換。リンク系プレースホルダは空に（URLは最後に別付け）"""
    body = template
    for k in ("{title}", "{タイトル}"):
        body = body.replace(k, title)
    for k in ("{program}", "{番組名}"):
        body = body.replace(k, program)

    # リンク系は空（あとで必ずURL付加）
    for k in ["{link}", "{URL}", "{Url}", "{url}", "{エピソードURL}"]:
        body = body.replace(k, "").rstrip()

    return body.replace("\r", "").rstrip()


def extract_prefix(template: str) -> str:
    """テンプレ先頭の固定フレーズ（{title}/{program}/{link} より前）"""
    keys = ["{title}", "{タイトル}", "{program}", "{番組名}", "{link}", "{URL}", "{Url}", "{url}", "{エピソードURL}"]
    idxs = [template.find(k) for k in keys if k in template]
    cut = min([i for i in idxs if i >= 0], default=len(template))
    return template[:cut].strip()


# ========= 文字数計算（URL=23固定／ASCII=1・非ASCII=2） =========
def weighted_len_no_urls(s: str) -> int:
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in s)


def x_length(s: str) -> int:
    total = 0
    last = 0
    for m in RE_URL_ANY.finditer(s):
        seg = s[last : m.start()]
        total += weighted_len_no_urls(seg)
        total += TCO_URL_LEN
        last = m.end()
    total += weighted_len_no_urls(s[last:])
    return total


def smart_truncate(title: str, keep: int) -> str:
    if keep >= len(title):
        return title
    if keep <= 0:
        return ""
    return title[: keep - 1] + "…"


def compose_with_title(template: str, title: str, program: str, link: str) -> str:
    body = render_body_without_link(template, title, program)
    link = normalize_link(link)
    return (body + ("\n" + link if link else "")).strip()


def compose_text(
    template: str, title: str, program: str, link: str, limit: int = MAX_TWEET_LIMIT
) -> str:
    """
    ルール:
      - URLは必ず付ける（URLは23文字換算）
      - 定型文（テンプレ先頭の固定フレーズ）とタグは必ず残す
      - タイトルのみを段階的に省略して280以内に収める
      - どうしても超える場合のみ、番組名を空にして再試行（稀）
    """
    pref = extract_prefix(template)

    # まず元タイトルで試す
    cand = compose_with_title(template, title, program, link)
    if x_length(cand) <= limit:
        return cand

    # タイトルを二分探索で最長に合わせる
    lo, hi = 0, len(title)
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        cand_mid = compose_with_title(template, smart_truncate(title, mid), program, link)
        if x_length(cand_mid) <= limit:
            best = cand_mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best

    # タイトル0でも超える場合、番組名を空に（定型文+タグは維持）
    cand_progless = compose_with_title(template, "", "", link)
    if x_length(cand_progless) <= limit and pref:
        return cand_progless

    # 最後の保険：定型文+URLのみ
    cand_prefix = (pref + ("\n" + normalize_link(link) if link else "")).strip()
    if x_length(cand_prefix) <= limit and pref:
        return cand_prefix

    # それでもダメならURLのみ（ほぼ起きない）
    return normalize_link(link)


# ========= メイン =========
def main() -> None:
    # feeds.json のロード
    try:
        with open("feeds.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        logger.error("feeds.json の読み込みに失敗しました: %s", e)
        return

    state = load_state()
    candidates: List[Dict[str, Any]] = []  # {ts, uid, text}

    feeds = cfg.get("feeds", [])
    if not isinstance(feeds, list):
        logger.error("feeds.json の形式が不正です（feeds が list ではありません）")
        return

    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        if feed.get("type") != "podcast":
            continue

        url = feed.get("url")
        tmpl = feed.get("template")
        program = feed.get("program_name", "")

        if not url or not tmpl:
            logger.warning("feed定義が不足: url/template が必要です: %s", feed)
            continue

        logger.info("Fetching RSS: %s", url)
        parsed = feedparser.parse(url)
        if parsed.bozo:
            logger.warning("RSS parse 警告: %s", getattr(parsed, "bozo_exception", None))

        entries = entries_newest_first(parsed)
        if not entries:
            logger.info("RSSにエントリが見つかりませんでした: %s", url)
            continue

        for entry in entries[:CHECK_ITEMS]:
            uid_src = (
                getattr(entry, "id", None)
                or getattr(entry, "guid", None)
                or getattr(entry, "link", None)
                or getattr(entry, "title", None)
            )
            # URL + エントリ由来情報で安定UID
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()

            if uid in state:
                continue

            if minutes_since(entry) < FRESH_WAIT_MIN:
                logger.info("[WAIT] fresh episode（%s）: 反映待ち", getattr(entry, "title", ""))
                continue

            # Apple → Spotify 優先（mp3は原則使わない）
            link = pick_best_link_for_podcast(entry, feed)
            if not link:
                logger.info("[INFO] Apple/Spotify URL未解決: %s", (getattr(entry, "title", "") or "").strip())
                continue

            title = shorten_title(getattr(entry, "title", "") or "", maxlen=TITLE_MAXLEN)
            text = compose_text(tmpl, title, program, link, limit=MAX_TWEET_LIMIT)
            ts = entry_timestamp(entry)
            candidates.append({"ts": ts, "uid": uid, "text": text})

    if not candidates:
        logger.info("[INFO] 投稿候補はありません（今回の実行）")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    logger.info("Posting to X: %s", chosen["text"])

    try:
        status, body = post_to_x(chosen["text"])
    except Exception as e:
        logger.error("X投稿で例外: %s", e)
        return

    if status < 300:
        state[chosen["uid"]] = int(time.time())
        save_state(state)
        logger.info("[OK] posted podcast: status=%s", status)
    else:
        logger.warning("[WARN] podcast post failed (%s): %s", status, body)


if __name__ == "__main__":
    main()
