#!/usr/bin/env python3
"""Shared invariants for a funny-clip bank that can grow with new episodes."""

from __future__ import annotations

BASELINE_MIN_EPISODE_COVERAGE = 127


def validate_dynamic_topology(bank: list[dict]) -> dict[str, int]:
    base_items = [item for item in bank if not item.get("parent_id")]
    base_source_urls = [str(item.get("source_url") or "").strip() for item in base_items]
    all_source_urls = {str(item.get("source_url") or "").strip() for item in bank}

    errors: list[str] = []
    if len(base_items) < BASELINE_MIN_EPISODE_COVERAGE:
        errors.append(
            f"base episode coverage fell below {BASELINE_MIN_EPISODE_COVERAGE}"
        )
    if any(not url.startswith("https://listen.style/p/reelpal/") for url in base_source_urls):
        errors.append("a base clip has an invalid LISTEN episode URL")
    if len(set(base_source_urls)) != len(base_source_urls):
        errors.append("more than one base clip exists for an episode URL")
    if all_source_urls != set(base_source_urls):
        errors.append("a clip does not resolve to a registered base episode URL")
    if errors:
        raise RuntimeError("canonical bank topology is invalid: " + "; ".join(errors))

    return {
        "clips": len(bank),
        "base_episodes": len(base_items),
        "extra_clips": len(bank) - len(base_items),
    }
