#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from buffer_client import graphql, resolve_x_channel_id

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_DIR = ROOT / "analytics"
HISTORY_PATH = ANALYTICS_DIR / "x_metrics_history.json"
REPORT_PATH = ANALYTICS_DIR / "X_PERFORMANCE.md"
JST = ZoneInfo("Asia/Tokyo")
REPORT_DAYS = 28
URL_RE = re.compile(r"https?://\S+")


def gql_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_channel_context() -> dict:
    channel_id = resolve_x_channel_id()
    query = f"""
    query {{
      channel(input: {{ id: {gql_string(channel_id)} }}) {{
        id organizationId displayName name service timezone
        metadata {{ ... on TwitterMetadata {{ subscriptionType }} }}
      }}
    }}
    """
    channel = (graphql(query).get("data", {}).get("channel") or {})
    if not channel.get("organizationId"):
        raise RuntimeError("Buffer channel query returned no organizationId")
    return channel


def fetch_posts(org_id: str, channel_id: str) -> list[dict]:
    posts: list[dict] = []
    after: str | None = None

    for _ in range(10):
        after_arg = f", after: {gql_string(after)}" if after else ""
        query = f"""
        query {{
          posts(
            first: 100{after_arg}
            input: {{
              organizationId: {gql_string(org_id)}
              filter: {{ status: [sent], channelIds: [{gql_string(channel_id)}] }}
            }}
          ) {{
            edges {{
              node {{
                id text status createdAt sentAt dueAt externalLink metricsUpdatedAt
                metrics {{ type name value unit }}
              }}
            }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}
        """
        result = graphql(query).get("data", {}).get("posts") or {}
        posts.extend(edge.get("node") or {} for edge in result.get("edges", []))
        page_info = result.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

    return [p for p in posts if p.get("id")]


def load_json_posts(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(x.get("text", "")).strip() for x in data if str(x.get("text", "")).strip()}
    except Exception:
        return set()


def survey_roots() -> set[str]:
    path = ROOT / "tweets.txt"
    if not path.exists():
        return set()
    roots = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        line = URL_RE.sub("", line)
        line = line.replace("#リルパル", "").replace("#ReelPal", "")
        roots.add(" ".join(line.split()).strip())
    return {x for x in roots if x}


QUICK_TEXTS = load_json_posts(ROOT / "data" / "quick_reply_posts.json")
DISCUSSION_TEXTS = load_json_posts(ROOT / "data" / "discussion_posts.json") | load_json_posts(
    ROOT / "data" / "discussion_posts_extra.json"
)
SURVEY_ROOTS = survey_roots()


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def classify_post(text: str) -> str:
    raw = (text or "").strip()
    normalized = normalize_text(raw)
    if raw in QUICK_TEXTS:
        return "quick_reply"
    if raw in DISCUSSION_TEXTS:
        return "discussion"
    if normalized in SURVEY_ROOTS:
        return "survey"
    if "リルパル" in raw and (
        "というニュース" in raw or "という話題" in raw or "こういうニュース" in raw
    ):
        return "trend_tiein"
    if raw.startswith("🎧 新着エピソード公開"):
        return "new_podcast"
    if raw.startswith("📝 新着note"):
        return "new_note"
    if raw.startswith("今夜の一本は『"):
        return "archive_friday"
    if "週末" in raw and ("3本" in raw or "①" in raw):
        return "archive_weekend"
    if "リルパルの沼から" in raw and "3本" in raw:
        return "archive_weekend"
    if "リクエスト" in raw and "フォーム" in raw:
        return "survey_reply"
    if "リルパルの過去回はこちら" in raw:
        return "archive_reply"
    return "other"


def metrics_dict(post: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for metric in post.get("metrics") or []:
        metric_type = str(metric.get("type") or "")
        value = metric.get("value")
        if not metric_type or value is None:
            continue
        try:
            out[metric_type] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def load_history() -> dict[str, dict]:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def update_history(history: dict[str, dict], posts: list[dict]) -> dict[str, dict]:
    collected_at = datetime.now(timezone.utc).isoformat()
    for post in posts:
        post_id = str(post.get("id"))
        text = str(post.get("text") or "").strip()
        published = parse_dt(post.get("sentAt")) or parse_dt(post.get("dueAt")) or parse_dt(post.get("createdAt"))
        history[post_id] = {
            "id": post_id,
            "text": text,
            "kind": classify_post(text),
            "status": post.get("status"),
            "createdAt": post.get("createdAt"),
            "sentAt": post.get("sentAt"),
            "dueAt": post.get("dueAt"),
            "publishedAt": published.isoformat() if published else None,
            "externalLink": post.get("externalLink"),
            "metricsUpdatedAt": post.get("metricsUpdatedAt"),
            "metrics": metrics_dict(post),
            "lastCollectedAt": collected_at,
        }
    return history


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def med(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def fmt_number(value: float) -> str:
    return f"{int(round(value)):,}"


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def report_rows(records: list[dict], key_fn) -> list[tuple]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        groups[str(key_fn(rec))].append(rec)

    rows = []
    for key, recs in groups.items():
        imps = [r["metrics"]["impressions"] for r in recs if "impressions" in r.get("metrics", {})]
        if not imps:
            continue
        ers = [r["metrics"]["engagementRate"] for r in recs if "engagementRate" in r.get("metrics", {})]
        rows.append((
            key,
            len(imps),
            sum(imps),
            avg(imps),
            med(imps),
            avg(ers),
            sum(r["metrics"].get("comments", 0) for r in recs),
            sum(r["metrics"].get("reposts", 0) for r in recs),
        ))
    rows.sort(key=lambda row: row[4], reverse=True)
    return rows


def markdown_table(rows: list[tuple], first_header: str) -> str:
    if not rows:
        return "まだ比較に使えるインプレッションデータがありません。\n"
    lines = [
        f"| {first_header} | n | 総imp | 平均imp | 中央値imp | 平均ER | 返信 | RP |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, n, total, average, median_value, er, comments, reposts in rows:
        lines.append(
            f"| {key} | {n} | {fmt_number(total)} | {fmt_number(average)} | {fmt_number(median_value)} | {fmt_pct(er)} | {fmt_number(comments)} | {fmt_number(reposts)} |"
        )
    return "\n".join(lines) + "\n"


def build_report(history: dict[str, dict], channel: dict) -> str:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=REPORT_DAYS)
    records = []

    for rec in history.values():
        published = parse_dt(rec.get("publishedAt"))
        if not published or published < cutoff:
            continue
        if "impressions" not in rec.get("metrics", {}):
            continue
        item = dict(rec)
        local = published.astimezone(JST)
        item["weekday"] = "月火水木金土日"[local.weekday()]
        item["local_time"] = local
        if 6 <= local.hour < 12:
            item["time_bucket"] = "朝 6–12"
        elif 12 <= local.hour < 18:
            item["time_bucket"] = "昼 12–18"
        else:
            item["time_bucket"] = "夜 18–6"
        records.append(item)

    subscription = ((channel.get("metadata") or {}).get("subscriptionType") or "unknown")
    name = channel.get("displayName") or channel.get("name") or "X channel"
    updated = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    lines = [
        "# X Performance Dashboard", "",
        f"- 更新: {updated}",
        f"- 対象: {name}",
        f"- Bufferが返すX subscriptionType: `{subscription}`",
        f"- 集計窓: 直近{REPORT_DAYS}日",
        f"- 履歴保存済み投稿: {len(history)}",
        f"- imp取得済み投稿: {len(records)}",
        "",
        "> Bufferの投稿メトリクスは1日1回程度更新されるため、直近24時間の数字は未確定の場合があります。",
        "",
        "## 投稿タイプ別", "",
        markdown_table(report_rows(records, lambda r: r.get("kind", "other")), "タイプ").rstrip(),
        "", "## 曜日別", "",
        markdown_table(report_rows(records, lambda r: r.get("weekday", "?")), "曜日").rstrip(),
        "", "## 時間帯別", "",
        markdown_table(report_rows(records, lambda r: r.get("time_bucket", "?")), "時間帯").rstrip(),
        "", "## インプレッション上位", "",
    ]

    top = sorted(records, key=lambda r: r.get("metrics", {}).get("impressions", 0), reverse=True)[:10]
    if not top:
        lines.append("まだデータがありません。")
    else:
        for i, rec in enumerate(top, start=1):
            local = rec["local_time"].strftime("%m/%d %H:%M")
            text = normalize_text(rec.get("text", ""))
            if len(text) > 80:
                text = text[:79] + "…"
            imp = rec.get("metrics", {}).get("impressions", 0)
            er = rec.get("metrics", {}).get("engagementRate", 0)
            lines.append(f"{i}. **{fmt_number(imp)} imp / {fmt_pct(er)}** — {local} — `{rec.get('kind')}` — {text}")

    lines += ["", "## 読み方", ""]
    if len(records) < 12:
        lines.append("まだ標本が少ないので、現段階ではスケジュールや投稿形式を大きく変更しない。最低12投稿、できれば各タイプ3投稿以上たまってから比較する。")
    else:
        kind_rows = [r for r in report_rows(records, lambda r: r.get("kind", "other")) if r[1] >= 3]
        day_rows = [r for r in report_rows(records, lambda r: r.get("weekday", "?")) if r[1] >= 3]
        time_rows = [r for r in report_rows(records, lambda r: r.get("time_bucket", "?")) if r[1] >= 3]
        if kind_rows:
            lines.append(f"- 投稿タイプでは **{kind_rows[0][0]}** が中央値imp首位（n={kind_rows[0][1]}）。")
        if day_rows:
            lines.append(f"- 曜日では **{day_rows[0][0]}曜** が中央値imp首位（n={day_rows[0][1]}）。")
        if time_rows:
            lines.append(f"- 時間帯では **{time_rows[0][0]}** が中央値imp首位（n={time_rows[0][1]}）。")
        lines.append("- 観察データなので因果とは限らない。勝ちパターン候補として次のA/Bテストに使う。")

    lines += [
        "", "## 次の最適化ルール", "",
        "1. 各タイプ最低3投稿までは固定運用。",
        "2. 3投稿以上たまったら中央値impを主指標、返信数を副指標にする。",
        "3. 勝ちタイプを増やす時も、1回に変える要因は曜日・時間・文型のどれか1つだけ。",
        "4. 外部リンク投稿はリンクなし投稿と別カテゴリで比較する。",
        "5. 新着回の特殊な伸びは通常投稿と混ぜて評価しない。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    channel = get_channel_context()
    posts = fetch_posts(str(channel["organizationId"]), str(channel["id"]))
    history = update_history(load_history(), posts)
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(build_report(history, channel), encoding="utf-8")
    with_metrics = sum(1 for p in posts if metrics_dict(p))
    print(f"[OK] collected {len(posts)} sent X posts; with_metrics={with_metrics}; history={len(history)}")


if __name__ == "__main__":
    main()
