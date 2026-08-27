#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import html
import json
import os
import random
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

TOPICS_PATH = ROOT / "data" / "trend_episode_topics.json"
STATE_PATH = ROOT / "state_trend_tiein.json"
JST = ZoneInfo("Asia/Tokyo")
NEWS_MAX_AGE_HOURS = 30
EPISODE_COOLDOWN_DAYS = 14
MAX_SEEN_NEWS = 80
REQUEST_TIMEOUT = 15
MAX_ROOT_LEN = 275

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
AMBIGUOUS_EXACT_TERMS = {"国宝"}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def norm(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKC", value)
    return SPACE_RE.sub(" ", value).strip().casefold()


def load_topics() -> list[dict]:
    data = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise RuntimeError("trend_episode_topics.json is empty")
    return data


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_post_date", None)
            data.setdefault("seen_news", [])
            data.setdefault("episode_last_posted", {})
            return data
    except Exception:
        pass
    return {"last_post_date": None, "seen_news": [], "episode_last_posted": {}}


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


def news_key(entry, headline: str, source: str) -> str:
    seed = str(entry.get("id") or entry.get("guid") or entry.get("link") or "")
    seed += "\n" + source + "\n" + headline
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def score_candidate(topic: dict, headline: str, summary: str) -> tuple[int, list[str]]:
    h = norm(headline)
    body = norm(headline + " " + summary)
    exact_hits = [term for term in topic.get("exact_terms", []) if norm(term) in h]
    related_hits = [term for term in topic.get("related_terms", []) if norm(term) in h]
    context_hits = [term for term in topic.get("context_terms", []) if norm(term) in body]

    if exact_hits:
        if any(term in AMBIGUOUS_EXACT_TERMS for term in exact_hits) and not context_hits:
            return 0, []
        return 10 + min(4, len(exact_hits) - 1) + min(3, len(context_hits)), exact_hits + context_hits

    if related_hits and context_hits:
        return 6 + min(4, (len(related_hits) - 1) * 2) + min(2, len(context_hits)), related_hits + context_hits

    return 0, []


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
    work: list[tuple[dict, str]] = []
    for topic in topics:
        for query in topic.get("search_queries", []):
            if str(query).strip():
                work.append((topic, str(query).strip()))

    results: list[tuple[dict, str, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_feed, query): (topic, query) for topic, query in work}
        for future in as_completed(futures):
            topic, query = futures[future]
            try:
                results.append((topic, query, future.result()))
            except Exception as exc:
                print(f"[WARN] trend feed failed: {query}: {exc}")

    dedupe: dict[str, dict] = {}
    for topic, query, entries in results:
        url = str(topic.get("listen_url") or "").strip()
        if not url or episode_on_cooldown(state, url, now):
            continue

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
            if not headline:
                continue
            summary = clean_summary(entry)
            key = news_key(entry, headline, source)
            if key in seen_news:
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

    variants = [
        f"{source}で「{headline}」というニュース。\n\nこの話題で思い出したのが『{episode}』。リルパルでは「{angle}」という方向から話しています。",
        f"「{headline}」という話題を見て、『{episode}』を思い出した。\n\nリルパルでも「{angle}」あたりをけっこう話しています。",
        f"{source}の「{headline}」が気になる。\n\nこういうニュースから連想するのが『{episode}』。リルパルでは「{angle}」まで寄り道しています。",
    ]
    seed = int(candidate["key"][:8], 16)
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

    reply = f"🎧 関連回\n『{episode}』\n{topic['listen_url']}"
    if len(reply) > 280:
        reply = f"🎧 関連回はこちら\n{topic['listen_url']}"
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
    seen = [str(x) for x in state.get("seen_news", []) if str(x) != chosen["key"]]
    state["seen_news"] = (seen + [chosen["key"]])[-MAX_SEEN_NEWS:]
    episode_last = dict(state.get("episode_last_posted") or {})
    episode_last[chosen["topic"]["listen_url"]] = now.isoformat()
    state["episode_last_posted"] = episode_last
    save_state(state)
    print(f"[OK] Buffer accepted trend episode tie-in: {post_id}")


if __name__ == "__main__":
    main()
