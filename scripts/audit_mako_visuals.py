"""Read-only audit of currently approved film story visuals; never posts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from urllib.parse import urlsplit
from time import sleep
from pathlib import Path

import requests

from scripts.mako_film_stories import load_bank, visual_assets, MEDIA_USER_AGENT

MAX_BYTES = 5 * 1024 * 1024
MIME = {"image/jpeg", "image/png", "image/webp"}


def check_asset(task):
    story_id, field, url = task
    record = {"story_id": story_id, "field": field, "url": url, "ok": False}
    try:
        with requests.get(
            url, stream=True, allow_redirects=False, timeout=(8, 20),
            headers={"User-Agent": MEDIA_USER_AGENT},
        ) as response:
            record["http_status"] = response.status_code
            if response.status_code in (429, 503):
                record["retry_after"] = response.headers.get("Retry-After")
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("HTTP response must be 200; redirects are not followed")
            mime = response.headers.get("Content-Type", "").split(";")[0].lower()
            record["mime"] = mime
            if mime not in MIME:
                raise ValueError("Unsupported image MIME type")
            declared = int(response.headers.get("Content-Length", "0"))
            if declared > MAX_BYTES:
                raise ValueError("Declared size exceeds 5 MiB")
            size = 0
            head = bytearray()
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Downloaded size exceeds 5 MiB")
                if len(head) < 12:
                    head.extend(chunk[:12-len(head)])
            valid = (
                mime == "image/jpeg" and head.startswith(b"\xff\xd8\xff")
                or mime == "image/png" and head.startswith(b"\x89PNG\r\n\x1a\n")
                or mime == "image/webp" and head.startswith(b"RIFF") and head[8:12] == b"WEBP"
            )
            if not valid:
                raise ValueError("Image signature and MIME type do not agree")
            record.update(ok=True, bytes=size)
    except (requests.RequestException, ValueError) as exc:
        record["error"] = str(exc)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("mako-visual-audit.json"))
    parser.add_argument("--min-per-day", type=int, default=2)
    args = parser.parse_args()
    rows = load_bank(require_full=True)
    tasks = [(row["id"], field, url) for row in rows for field, url in visual_assets(row)]
    # No concurrent requests and no retries against a throttled host.
    # A blocked host is unverified, not an invalid copyright record.
    results = []
    blocked = {}
    for task in tasks:
        host = urlsplit(task[2]).hostname
        if host in blocked:
            results.append({"story_id": task[0], "field": task[1], "url": task[2],
                            "ok": False, "status": "deferred_rate_limit",
                            "error": f"Host deferred after throttling; Retry-After={blocked[host]}"})
            continue
        result = check_asset(task)
        results.append(result)
        if result.get("http_status") in (429, 503):
            blocked[host] = result.get("retry_after") or "unspecified"
        sleep(1)

    registered = {task[0] for task in tasks}
    reachable = {result["story_id"] for result in results if result["ok"]}
    weekdays = Counter(row["weekday"] for row in rows if row["id"] in reachable)
    report = {
        "total_stories": len(rows),
        "registered_stories": len(registered),
        "reachable_stories": len(reachable),
        "reachable_by_weekday": {str(day): weekdays[day] for day in range(7)},
        "assets": results,
        "deferred_hosts": blocked,
        "note": "Network accessibility only; does not independently certify legal rights or X publication.",
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "assets"}, ensure_ascii=False))
    for result in results:
        if not result["ok"]:
            print("[WARN]", result["story_id"], result["field"], result.get("error", "unknown"))
    if any(weekdays[day] < args.min_per_day for day in range(7)):
        raise SystemExit("Insufficient reachable image-backed stories for a safe release")


if __name__ == "__main__":
    main()
