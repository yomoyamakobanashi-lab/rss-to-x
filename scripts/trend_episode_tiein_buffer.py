#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from buffer_client import post_thread
from scripts.episode_links import render_episode_reply

TOPICS_PATH = ROOT / "data" / "trend_episode_topics.json"
STATE_PATH = ROOT / "state_trend_tiein.json"
DAILY_STATE_PATH = ROOT / "state_daily_content.json"
JST = ZoneInfo("Asia/Tokyo")
NEWS_MAX_AGE_HOURS = 30
EPISODE_COOLDOWN_DAYS = 14
MAX_SEEN_NEWS = 80
REQUEST_TIMEOUT = 15
MAX_ROOT_LEN = 275
MIN_POST_GAP_MINUTES = 90
BASELINE_SLOT_MINUTES = (8 * 60 + 10, 17 * 60 + 10, 21 * 60 + 20)

# The curated searches remain the highest-confidence path.  These broad feeds
# let the same matcher discover timely hooks for the full verified archive
# without making one Google News request per episode.
BROAD_DISCOVERY_QUERIES = (
    "映画 公開 予告",
    "映画 続編 キャスト",
    "Netflix 映画 ディズニー ピクサー",
    "ホラー映画 SF映画 アニメ映画",
)

AUTO_TOPIC_BANKS = (
    ROOT / "data" / "funny_clip_posts_all_episodes.json",
    ROOT / "data" / "funny_clip_legacy_canonical.json",
)

AUTO_CONTEXT_TERMS = (
    "映画", "作品", "公開", "予告", "続編", "シリーズ", "上映", "配信",
    "興行", "監督", "主演", "キャスト", "Netflix", "ディズニー", "ピクサー",
)

# Short or generic tokens create convincing-looking false matches.  They are
# deliberately excluded from automatic expansion; hand-curated topics may
# still opt in with additional context checks.
AUTO_BLOCKED_TERMS = {
    "映画", "ホラー", "アニメ", "神話", "特撮", "パンダ", "コンゴ", "犬",
    "手話", "任天堂", "最新映画情報", "口に関するアンケート", "michael", "her",
    "lucy", "mama", "国宝", "wicked", "hbo", "netflix", "mcu", "uma",
    "ホラー映画", "アクション映画", "コメディ映画", "sfホラー", "ラブロマンス",
    "クリスマス", "ヒューマンドラマ", "ノンフィクション映画", "ジャンルシフト",
    "大どんでん返し", "戦争映画", "和製ファンタジー映画", "3d映画",
    "ドキュメンタリー", "ホラーコメディ", "horror", "comedyfilm", "romancefilm",
    "クライムコメディ", "humandrama", "warcinema", "apocalypse", "genreshift",
    "you’re", "mandom", "監督", "主演", "配信", "replay",
}

ALLOWED_SOURCE_FRAGMENTS = (
    "映画.com",
    "シネマトゥデイ",
    "映画ナタリー",
    "ORICON NEWS",
    "シネマカフェ",
    "THE RIVER",
    "Variety Japan",
    "Real Sound",
    "IGN Japan",
    "SCREEN ONLINE",
    "クランクイン",
    "ぴあ映画",
    "CINRA",
    "KAI-YOU",
    "MOVIE WALKER PRESS",
    "Fan's Voice",
    "Disney",
    "ディズニー",
    "松竹",
)
BLOCKED_HEADLINE_TERMS = (
    "訃報",
    "死去",
    "逝去",
    "亡くな",
    "死亡",
    "追悼",
    "事故",
    "被害",
    "殺害",
    "自殺",
    "逮捕",
    "起訴",
    "性加害",
    "不祥事",
    "病気",
    "闘病",
    "災害",
    "炎上",
)
AMBIGUOUS_EXACT_TERMS = {"国宝"}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def norm(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKC", value)
    return SPACE_RE.sub(" ", value).strip().casefold()


def _clean_auto_term(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.strip(" #＃　\t\r\n『』「」〖〗【】\"'“”*,:;()（）")
    value = re.sub(r"[’']s$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"(?:とか|など|事情)$", "", value).strip()
    return value


def extract_episode_terms(title: str) -> list[str]:
    title = unicodedata.normalize("NFKC", str(title or ""))
    candidates = re.findall(r"『([^』]{2,60})』", title)
    candidates += re.findall(r"[#＃]([^#＃\s　、。…『』「」【】〖〗〜～!?！？:：,，]+)", title)
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        term = _clean_auto_term(candidate)
        key = norm(term)
        if not key or key in AUTO_BLOCKED_TERMS or key in seen:
            continue
        if len(key) < 3 or len(term) > 42:
            continue
        # Reject accidental captures such as "ティム・バートン映画『".
        if (
            "映画『" in term
            or "企画" in term
            or term.endswith("映画")
            or term.isdigit()
            or (term.isascii() and len(term) < 6)
        ):
            continue
        seen.add(key)
        out.append(term)
    return out[:4]


def load_auto_topics(curated: list[dict]) -> list[dict]:
    curated_urls = {str(item.get("listen_url") or "").strip() for item in curated}
    used_terms = {
        norm(term)
        for item in curated
        for term in item.get("exact_terms", [])
        if str(term).strip()
    }
    topics: list[dict] = []
    seen_urls: set[str] = set()
    for path in AUTO_TOPIC_BANKS:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            listen_url = str(row.get("source_url") or "").strip()
            if (
                not listen_url.startswith("https://listen.style/p/reelpal/")
                or listen_url in curated_urls
                or listen_url in seen_urls
            ):
                continue
            terms = [
                term for term in extract_episode_terms(str(row.get("episode_title") or ""))
                if norm(term) not in used_terms
            ]
            if not terms:
                continue
            seen_urls.add(listen_url)
            used_terms.update(norm(term) for term in terms)
            topics.append({
                "episode_title": terms[0],
                "listen_url": listen_url,
                "search_queries": [],
                "exact_terms": terms,
                "related_terms": [],
                "context_terms": list(AUTO_CONTEXT_TERMS),
                "angle": "",
                "discovery_mode": True,
            })
    return topics


def load_topics() -> list[dict]:
    data = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise RuntimeError("trend_episode_topics.json is empty")
    for item in data:
        item.setdefault("discovery_mode", False)
    return data + load_auto_topics(data)


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_post_date", None)
            data.setdefault("last_posted_at", None)
            data.setdefault("seen_news", [])
            data.setdefault("episode_last_posted", {})
            return data
    except Exception:
        pass
    return {
        "last_post_date": None,
        "last_posted_at": None,
        "seen_news": [],
        "episode_last_posted": {},
    }


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def google_news_url(query: str) -> str:
    q = quote_plus(f"{query} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_feed(query: str) -> list[dict]:
    response = requests.get(
        google_news_url(query),
        headers={"User-Agent": "ReelPalTrendBot/1.0 (+https://listen.style/p/reelpal)"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    return list(feed.entries or [])


def source_name(entry) -> str:
    source = entry.get("source") or {}
    if isinstance(source, dict):
        return str(source.get("title") or "").strip()
    return str(getattr(source, "title", "") or "").strip()


def allowed_source(source: str) -> bool:
    source_n = norm(source)
    return bool(source_n) and any(norm(fragment) in source_n for fragment in ALLOWED_SOURCE_FRAGMENTS)


def published_at(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def clean_summary(entry) -> str:
    raw = str(entry.get("summary") or entry.get("description") or "")
    return SPACE_RE.sub(" ", TAG_RE.sub(" ", html.unescape(raw))).strip()


def clean_headline(entry, source: str) -> str:
    title = SPACE_RE.sub(" ", html.unescape(str(entry.get("title") or ""))).strip()
    suffix = f" - {source}" if source else ""
    if suffix and title.endswith(suffix):
        title = title[: -len(suffix)].rstrip()
    return title


def headline_is_sensitive(headline: str) -> bool:
    h = norm(headline)
    return any(norm(term) in h for term in BLOCKED_HEADLINE_TERMS)


def news_key(entry, headline: str, source: str) -> str:
    seed = str(entry.get("id") or entry.get("guid") or entry.get("link") or "")
    seed += "\n" + source + "\n" + headline
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def score_candidate(topic: dict, headline: str, summary: str) -> tuple[int, list[str]]:
    h = norm(headline)
    body = norm(headline + " " + summary)
    exact_hits = [term for term in topic.get("exact_terms", []) if contains_term(h, term)]
    related_hits = [term for term in topic.get("related_terms", []) if contains_term(h, term)]
    context_hits = [term for term in topic.get("context_terms", []) if norm(term) in body]

    if exact_hits:
        if any(term in AMBIGUOUS_EXACT_TERMS for term in exact_hits) and not context_hits:
            return 0, []
        return 10 + min(4, len(exact_hits) - 1) + min(3, len(context_hits)), exact_hits + context_hits

    if related_hits and context_hits:
        return 6 + min(4, (len(related_hits) - 1) * 2) + min(2, len(context_hits)), related_hits + context_hits

    return 0, []


def contains_term(normalized_text: str, term: str) -> bool:
    needle = norm(term)
    if not needle:
        return False
    # Long titles are distinctive enough for substring matching.  Short film
    # names need boundaries so, for example, 「リング」 does not match
    # 「スプリング」.
    if len(needle) > 4:
        return needle in normalized_text
    start = 0
    while True:
        index = normalized_text.find(needle, start)
        if index < 0:
            return False
        left = normalized_text[index - 1] if index > 0 else ""
        right_index = index + len(needle)
        right = normalized_text[right_index] if right_index < len(normalized_text) else ""
        if not (left and left.isalnum()) and not (right and right.isalnum()):
            return True
        start = index + 1


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(timezone.utc)


def too_close_to_daily_content(now: datetime) -> bool:
    local = now.astimezone(JST)
    minute_of_day = local.hour * 60 + local.minute
    # Protect the next nominal baseline slot even if GitHub's cron is delayed.
    if min(abs(minute_of_day - slot) for slot in BASELINE_SLOT_MINUTES) < MIN_POST_GAP_MINUTES:
        return True

    try:
        data = json.loads(DAILY_STATE_PATH.read_text(encoding="utf-8"))
        today = (data.get("days") or {}).get(local.date().isoformat()) or {}
        values = (today.get("posted_at") or {}).values()
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        values = []
    for value in values:
        posted = parse_iso(str(value))
        if posted and abs((now - posted).total_seconds()) < MIN_POST_GAP_MINUTES * 60:
            return True
    return False


def episode_on_cooldown(state: dict, url: str, now: datetime) -> bool:
    value = (state.get("episode_last_posted") or {}).get(url)
    if not value:
        return False
    try:
        posted = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return now - posted < timedelta(days=EPISODE_COOLDOWN_DAYS)


def collect_candidates(topics: list[dict], state: dict) -> list[dict]:
    now = datetime.now(timezone.utc)
    seen_news = set(str(x) for x in state.get("seen_news", []))
    work: list[tuple[str, dict | None, str]] = []
    for topic in topics:
        for query in topic.get("search_queries", []):
            if str(query).strip():
                work.append(("specific", topic, str(query).strip()))
    for query in BROAD_DISCOVERY_QUERIES:
        work.append(("broad", None, query))

    results: list[tuple[str, dict | None, str, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_feed, query): (mode, topic, query)
            for mode, topic, query in work
        }
        for future in as_completed(futures):
            mode, topic, query = futures[future]
            try:
                results.append((mode, topic, query, future.result()))
            except Exception as exc:
                print(f"[WARN] trend feed failed: {query}: {exc}")

    dedupe: dict[str, dict] = {}
    for mode, selected_topic, query, entries in results:
        for entry in entries:
            source = source_name(entry)
            if not allowed_source(source):
                continue
            published = published_at(entry)
            if not published:
                continue
            age_hours = (now - published).total_seconds() / 3600
            if age_hours < -1 or age_hours > NEWS_MAX_AGE_HOURS:
                continue

            headline = clean_headline(entry, source)
            if not headline or headline_is_sensitive(headline):
                continue
            summary = clean_summary(entry)
            key = news_key(entry, headline, source)
            if key in seen_news:
                continue

            candidate_topics = [selected_topic] if mode == "specific" else topics
            for topic in candidate_topics:
                if not topic:
                    continue
                url = str(topic.get("listen_url") or "").strip()
                if not url or episode_on_cooldown(state, url, now):
                    continue
                score, matches = score_candidate(topic, headline, summary)
                if score < 6:
                    continue

                candidate = {
                    "key": key,
                    "score": score,
                    "published": published,
                    "headline": headline,
                    "source": source,
                    "news_url": str(entry.get("link") or "").strip(),
                    "query": query,
                    "matches": matches,
                    "topic": topic,
                }
                previous = dedupe.get(key)
                if previous is None or candidate["score"] > previous["score"]:
                    dedupe[key] = candidate

    return sorted(dedupe.values(), key=lambda c: (c["score"], c["published"]), reverse=True)


def clip(text: str, limit: int) -> str:
    text = SPACE_RE.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compose(candidate: dict) -> tuple[str, str]:
    topic = candidate["topic"]
    source = clip(candidate["source"], 24)
    headline = clip(candidate["headline"], 100)
    episode = clip(str(topic.get("episode_title") or ""), 58)
    angle = clip(str(topic.get("angle") or ""), 105)

    seed = int(candidate["key"][:8], 16)
    if topic.get("discovery_mode"):
        variants = [
            f"映画好きに聞きたい。\n\n{source}の「{headline}」。\nこのニュース、第一印象は「楽しみ」「様子見」どっち？",
            f"映画好きに聞きたい。\n\n「{headline}」という話題。\nこれを見て、いま一番話したくなったことは何ですか？",
            f"映画好きに聞きたい。\n\n{source}で「{headline}」。\nこの話題、あなたはどう受け取りました？",
        ]
        root = variants[seed % len(variants)]
    else:
        variants = [
            f"{source}で「{headline}」というニュース。\n\nこの話題で思い出したのが『{episode}』。リルパルでは「{angle}」という方向から話しています。",
            f"「{headline}」という話題を見て、『{episode}』を思い出した。\n\nリルパルでも「{angle}」あたりをけっこう話しています。",
            f"{source}の「{headline}」が気になる。\n\nこういうニュースから連想するのが『{episode}』。リルパルでは「{angle}」まで寄り道しています。",
        ]
        root = variants[seed % len(variants)]
    if len(root) > MAX_ROOT_LEN:
        headline = clip(candidate["headline"], 70)
        angle = clip(str(topic.get("angle") or ""), 78)
        root = (
            f"「{headline}」という話題を見て、『{episode}』を思い出した。\n\n"
            f"リルパルでは「{angle}」という方向から話しています。"
        )
    if len(root) > MAX_ROOT_LEN:
        root = clip(root, MAX_ROOT_LEN)

    reply = render_episode_reply(
        title=topic.get("episode_title"),
        listen_url=topic.get("listen_url"),
        intro="🎧 リルパルの関連回を聴く",
    )
    return root, reply


def main() -> None:
    dry_run = str(os.getenv("TREND_TIEIN_DRY_RUN") or "").strip().lower() in {"1", "true", "yes"}
    topics = load_topics()
    state = load_state()
    now = datetime.now(timezone.utc)
    today_jst = now.astimezone(JST).date().isoformat()

    if not dry_run and state.get("last_post_date") == today_jst:
        print(f"[SKIP] trend tie-in already posted on {today_jst}")
        return
    if not dry_run and too_close_to_daily_content(now):
        print(f"[SKIP] discovery post would be within {MIN_POST_GAP_MINUTES} minutes of daily content")
        return

    candidates = collect_candidates(topics, state)
    if not candidates:
        print("[SKIP] no high-confidence movie trend matched a verified ReelPal episode")
        return

    chosen = candidates[0]
    root, reply = compose(chosen)
    print(
        f"[MATCH] score={chosen['score']} source={chosen['source']} "
        f"episode={chosen['topic']['episode_title']} headline={chosen['headline']}"
    )
    print(f"[POST ROOT] {root}")
    print(f"[POST REPLY] {reply}")

    if dry_run:
        print(f"[DRY RUN] {len(candidates)} candidate(s); no X post created")
        return

    post_id = post_thread([root, reply])
    state["last_post_date"] = today_jst
    state["last_posted_at"] = now.isoformat()
    seen = [str(x) for x in state.get("seen_news", []) if str(x) != chosen["key"]]
    state["seen_news"] = (seen + [chosen["key"]])[-MAX_SEEN_NEWS:]
    episode_last = dict(state.get("episode_last_posted") or {})
    episode_last[chosen["topic"]["listen_url"]] = now.isoformat()
    state["episode_last_posted"] = episode_last
    save_state(state)
    print(f"[OK] Buffer accepted trend episode tie-in: {post_id}")


if __name__ == "__main__":
    main()
