#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import calendar
import hashlib
import html
import json
import os
import re
import sys
import time
from typing import Any

import feedparser
import requests

from buffer_client import BufferError, post_text
from note_poster import load_state, save_state, x_length

MIN_AGE_MINUTES = int(os.getenv("NOTE_MIN_AGE_MINUTES", "15"))
MAX_AGE_HOURS = int(os.getenv("NOTE_MAX_CONTENT_AGE_HOURS", "48"))
TITLE_MAX_CHARS = 72
EXCERPT_MAX_CHARS = 72

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def entry_timestamp_utc(entry: Any) -> int | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return int(calendar.timegm(parsed))
    except Exception:
        return None


def entry_age_minutes(entry: Any) -> float | None:
    ts = entry_timestamp_utc(entry)
    if ts is None:
        return None
    return (time.time() - ts) / 60.0


def clean_excerpt(entry: Any) -> str:
    raw = (
        entry.get("summary")
        or entry.get("description")
        or ""
    )
    if not raw:
        content = entry.get("content") or []
        if content and isinstance(content[0], dict):
            raw = content[0].get("value") or ""

    text = html.unescape(TAG_RE.sub(" ", str(raw)))
    text = SPACE_RE.sub(" ", text).strip()

    # RSS本文にタイトルがそのまま先頭重複する場合は除く。
    title = SPACE_RE.sub(" ", str(entry.get("title") or "")).strip()
    if title and text.startswith(title):
        text = text[len(title):].lstrip(" ：:-—｜|")

    return text


def ellipsize(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def render_template(template: str, title: str, excerpt: str, program: str, link: str) -> str:
    values = {
        "{title}": title,
        "{タイトル}": title,
        "{excerpt}": excerpt,
        "{概要}": excerpt,
        "{program}": program,
        "{番組名}": program,
        "{link}": link,
        "{URL}": link,
        "{url}": link,
        "{記事URL}": link,
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)
    return text.strip()


def compose_note_announcement(template: str, title: str, excerpt: str, program: str, link: str) -> str:
    title = ellipsize(title, TITLE_MAX_CHARS)
    excerpt = ellipsize(excerpt, EXCERPT_MAX_CHARS)
    if not excerpt:
        excerpt = "映画や番組について、文章でもう一歩深掘りしました。"

    candidate = render_template(template, title, excerpt, program, link)
    if x_length(candidate) <= 280:
        return candidate

    # 概要を優先的に縮める。
    for n in range(min(len(excerpt), EXCERPT_MAX_CHARS), 7, -4):
        candidate = render_template(template, title, ellipsize(excerpt, n), program, link)
        if x_length(candidate) <= 280:
            return candidate

    # それでも収まらない場合はタイトルも縮める。
    for n in range(min(len(title), TITLE_MAX_CHARS), 19, -4):
        candidate = render_template(template, ellipsize(title, n), "記事を更新しました。", program, link)
        if x_length(candidate) <= 280:
            return candidate

    return f"📝 新着note\n{link}"


def fetch_note_og_url(url: str) -> str | None:
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        page = response.text
        for pattern in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            match = re.search(pattern, page, re.I)
            if match:
                return html.unescape(match.group(1)).strip()
    except Exception as exc:
        print(f"[WARN] OG image lookup failed: {exc}")
    return None


def main() -> None:
    try:
        with open("feeds.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        print(f"[ERROR] feeds.json: {exc}")
        sys.exit(2)

    state = load_state()

    for feed in cfg.get("feeds", []):
        if not isinstance(feed, dict) or feed.get("type") != "note":
            continue

        url = feed.get("url")
        template = feed.get("template")
        program = feed.get("program_name", "")
        if not url or not template:
            continue

        parsed = feedparser.parse(url)
        if getattr(parsed, "bozo", False):
            print(f"[WARN] note RSS parse warning: {getattr(parsed, 'bozo_exception', None)}")

        entries = list(parsed.entries or [])
        if not entries:
            print("[INFO] note RSS has no entries")
            return

        # 絶対に過去記事を掘り返さない。RSS先頭の最新1件だけを見る。
        entry = entries[0]
        age_minutes = entry_age_minutes(entry)
        if age_minutes is None:
            print("[INFO] newest note has no published/updated timestamp; skip safely")
            return
        if age_minutes < 0:
            print("[INFO] newest note timestamp is in the future; skip safely")
            return
        if age_minutes < MIN_AGE_MINUTES:
            print(f"[INFO] newest note is too fresh ({age_minutes:.1f} min); retry later")
            return
        if age_minutes > MAX_AGE_HOURS * 60:
            print(f"[INFO] newest note is older than {MAX_AGE_HOURS}h; no backfill")
            return

        uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
        uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
        if uid in state:
            print("[INFO] newest note already announced")
            return

        link = (entry.get("link") or "").strip()
        if not link:
            print("[INFO] newest note has no public link; retry later")
            return

        title = (entry.get("title") or "").strip()
        excerpt = clean_excerpt(entry)
        text = compose_note_announcement(template, title, excerpt, program, link)
        image_url = fetch_note_og_url(link)

        print(f"[INFO] posting newest note through Buffer; age={age_minutes:.1f}min image={'yes' if image_url else 'no'}")
        try:
            post_id = post_text(text, image_url=image_url)
        except BufferError as exc:
            print(f"[ERROR] Buffer note post failed: {exc}")
            sys.exit(4)

        # Bufferが受理した後だけ重複防止stateを進める。
        state[uid] = int(time.time())
        save_state(state)
        print(f"[OK] Buffer accepted note post: {post_id}")
        return

    print("[INFO] no note feed configured")


if __name__ == "__main__":
    main()
