#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPO = os.environ.get("GH_REPO", "yomoyamakobanashi-lab/rss-to-x")
BACKFILL = Path("data/generated_chapters/backfill.json")
AI_DIR = Path("data/generated_chapters/ai_backfill")
AUDIT = Path("data/generated_chapters/final_audit.json")
TITLE_PREFIX = "Spotifyチャプター貼り付け:"
MARKER_RE = re.compile(r"listen-chapter:([A-Za-z0-9_-]+)")
CHAPTER_LINE_RE = re.compile(
    r"^(?P<indent>\s*)\(?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s+(?P<title>.+)$"
)


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def all_issues() -> list[dict]:
    return json.loads(
        gh(
            "issue", "list", "--repo", REPO, "--state", "all", "--limit", "1000",
            "--json", "number,title,body,state"
        )
    )


def spotify_ts(ts: str) -> str:
    parts = [int(x) for x in str(ts).strip("()").split(":")]
    if len(parts) == 2:
        return f"({parts[0]:02d}:{parts[1]:02d})"
    if len(parts) == 3:
        return f"({parts[0]:02d}:{parts[1]:02d}:{parts[2]:02d})"
    raise ValueError(ts)


def normalize_issue_body(body: str) -> tuple[str, bool]:
    lines = body.splitlines()
    out: list[str] = []
    in_code = False
    changed = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            m = CHAPTER_LINE_RE.match(line)
            if m:
                replacement = f"{m.group('indent')}{spotify_ts(m.group('ts'))} {m.group('title')}"
                if replacement != line:
                    changed = True
                out.append(replacement)
                continue
        out.append(line)
    new_body = "\n".join(out)
    if body.endswith("\n"):
        new_body += "\n"
    return new_body, changed


def payload_for_episode(ep: dict) -> tuple[list[dict] | None, str, str]:
    if ep.get("status") == "ready" and len(ep.get("chapters") or []) >= 3:
        return ep["chapters"], "LISTEN自動チャプター", ""

    ai_path = AI_DIR / f"{ep['episode_id']}.json"
    if ai_path.exists():
        ai = json.loads(ai_path.read_text(encoding="utf-8"))
        chapters = ai.get("chapters") or []
        if len(chapters) >= 3:
            source = ai.get("source", "LISTEN transcript + Gemini")
            model = ai.get("model", "")
            return chapters, source, model
    return None, "", ""


def make_issue_body(ep: dict, chapters: list[dict], source: str, model: str) -> str:
    eid = ep["episode_id"]
    lines = [
        f"<!-- listen-chapter:{eid} -->",
        f"{source}をもとにSpotify用チャプターを用意しました。",
        "",
        "**Spotify for Creators の既存エピソード説明文は消さず、末尾へ以下をそのまま追加してください。**",
        "",
        "```text",
    ]
    lines.extend(f"{spotify_ts(c['timestamp'])} {c['title']}" for c in chapters)
    lines.extend(["```", ""])
    if ep.get("spotify_creator_url"):
        lines.append(f"Spotify / Podcasters: {ep['spotify_creator_url']}")
    lines.append(f"LISTEN: {ep['listen_episode_url']}")
    if model:
        lines.extend(["", f"生成モデル: {model}"])
    lines.extend([
        "",
        "サユリ回でこの括弧付きタイムスタンプ形式がSpotify上でクリック可能になることを実動確認済みです。",
        "貼り付け後に時刻が押せることを確認し、問題なければこのIssueをCloseしてください。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    data = json.loads(BACKFILL.read_text(encoding="utf-8"))
    episodes = data.get("episodes", [])

    issues = all_issues()
    marker_to_issue: dict[str, dict] = {}
    for issue in issues:
        for eid in MARKER_RE.findall(issue.get("body") or ""):
            marker_to_issue[eid] = issue

    created = 0
    unavailable_before: list[str] = []
    for ep in episodes:
        eid = ep["episode_id"]
        if eid in marker_to_issue:
            continue
        chapters, source, model = payload_for_episode(ep)
        if not chapters:
            unavailable_before.append(eid)
            continue
        title = f"{TITLE_PREFIX} {ep.get('title') or eid}"
        if len(title) > 245:
            title = title[:242] + "…"
        body = make_issue_body(ep, chapters, source, model)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(body)
            body_file = f.name
        try:
            url = gh("issue", "create", "--repo", REPO, "--title", title, "--body-file", body_file).strip()
            print(f"Created missing chapter Issue: {url}")
            created += 1
        finally:
            os.unlink(body_file)

    # Refresh, then normalize every still-open paste Issue to the verified Spotify format.
    issues = all_issues()
    normalized = 0
    for issue in issues:
        if issue.get("state") != "OPEN" and issue.get("state") != "open":
            continue
        if not (issue.get("title") or "").startswith(TITLE_PREFIX):
            continue
        body = issue.get("body") or ""
        new_body, changed = normalize_issue_body(body)
        if not changed:
            continue
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(new_body)
            body_file = f.name
        try:
            subprocess.run([
                "gh", "issue", "edit", str(issue["number"]), "--repo", REPO,
                "--body-file", body_file,
            ], check=True)
            normalized += 1
        finally:
            os.unlink(body_file)

    issues = all_issues()
    markers: dict[str, dict] = {}
    summary_number = None
    for issue in issues:
        body = issue.get("body") or ""
        for eid in MARKER_RE.findall(body):
            markers[eid] = issue
        if "listen-backfill-summary" in body:
            summary_number = issue["number"]

    episode_ids = {ep["episode_id"] for ep in episodes}
    covered_ids = episode_ids & set(markers)
    unresolved = [ep for ep in episodes if ep["episode_id"] not in covered_ids]
    ai_ids = {p.stem for p in AI_DIR.glob("*.json")} if AI_DIR.exists() else set()
    native_ready = sum(1 for ep in episodes if ep.get("status") == "ready")
    ai_ready = len(episode_ids & ai_ids)

    audit = {
        "episode_count": len(episodes),
        "chapter_issue_count": len(covered_ids),
        "native_listen_ready": native_ready,
        "ai_files": ai_ready,
        "created_this_run": created,
        "normalized_this_run": normalized,
        "unresolved_count": len(unresolved),
        "unresolved": [
            {
                "episode_id": ep["episode_id"],
                "title": ep.get("title", ""),
                "listen_episode_url": ep.get("listen_episode_url", ""),
                "status": ep.get("status", ""),
            }
            for ep in unresolved
        ],
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = [
        "<!-- listen-backfill-summary -->",
        "# Spotifyチャプター 過去回バックフィル状況",
        "",
        f"- LISTENで検出したエピソード: **{len(episodes)}件**",
        f"- Spotify貼り付け用Issue作成済み: **{len(covered_ids)}/{len(episodes)}件**",
        f"- LISTEN自動チャプター由来: **{native_ready}件**",
        f"- Gemini補完ファイル生成済み: **{ai_ready}件**",
        f"- 今回新規Issue作成: **{created}件**",
        f"- 今回Spotify実証形式へ正規化: **{normalized}件**",
        f"- 未解決: **{len(unresolved)}件**",
        "",
        "貼り付け形式はサユリ回で実動確認済みの `(MM:SS)` / `(HH:MM:SS)` 形式に統一しています。",
    ]
    if unresolved:
        summary.extend(["", "## 未解決エピソード"])
        for ep in unresolved:
            summary.append(
                f"- [{ep.get('title') or ep['episode_id']}]({ep.get('listen_episode_url','')}) — `{ep.get('status','')}` / `{ep['episode_id']}`"
            )
    else:
        summary.extend(["", "✅ **全エピソード分の貼り付け用Issueが揃いました。**"])

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
        f.write("\n".join(summary) + "\n")
        summary_file = f.name
    try:
        if summary_number:
            subprocess.run([
                "gh", "issue", "edit", str(summary_number), "--repo", REPO,
                "--title", "Spotifyチャプター 過去回バックフィル状況",
                "--body-file", summary_file,
            ], check=True)
        else:
            gh(
                "issue", "create", "--repo", REPO,
                "--title", "Spotifyチャプター 過去回バックフィル状況",
                "--body-file", summary_file,
            )
    finally:
        os.unlink(summary_file)

    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
