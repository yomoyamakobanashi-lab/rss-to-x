#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Small Buffer GraphQL client for publishing to the connected X channel.

Required secret:
  BUFFER_API_KEY

Optional selectors (only needed when auto-discovery is ambiguous):
  BUFFER_ORGANIZATION_ID
  BUFFER_CHANNEL_ID
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

import requests

BUFFER_API_URL = "https://api.buffer.com"
TIMEOUT = 30
REELPAL_TAG = "#リルパル"


class BufferError(RuntimeError):
    pass


def _ensure_reelpal_tag(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        raise BufferError("Post text is empty.")
    if REELPAL_TAG in clean:
        return clean
    tagged = f"{clean}\n\n{REELPAL_TAG}"
    if len(tagged) > 280:
        raise BufferError(
            f"Post exceeds 280 chars after required {REELPAL_TAG} tag is added: {len(tagged)}"
        )
    return tagged


def _api_key() -> str:
    key = (os.getenv("BUFFER_API_KEY") or "").strip()
    if not key:
        raise BufferError(
            "BUFFER_API_KEY is not set. Create a Buffer API key and add it as a GitHub Actions secret."
        )
    return key


def graphql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = requests.post(
        BUFFER_API_URL,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "rss-to-x-buffer/1.0",
        },
        json={"query": query, "variables": variables or {}},
        timeout=TIMEOUT,
    )
    if response.status_code >= 300:
        raise BufferError(f"Buffer HTTP {response.status_code}: {response.text[:500]}")

    try:
        payload = response.json()
    except Exception as exc:
        raise BufferError(f"Buffer returned non-JSON response: {response.text[:500]}") from exc

    if payload.get("errors"):
        messages = "; ".join(str(e.get("message", e)) for e in payload["errors"])
        raise BufferError(f"Buffer GraphQL error: {messages}")
    return payload


def _organizations() -> List[Dict[str, str]]:
    payload = graphql(
        """
        query GetOrganizations {
          account {
            organizations { id name }
          }
        }
        """
    )
    return payload.get("data", {}).get("account", {}).get("organizations", []) or []


def _channels(org_id: str) -> List[Dict[str, str]]:
    payload = graphql(
        """
        query GetChannels($orgId: OrganizationId!) {
          channels(input: { organizationId: $orgId, filter: { isLocked: false } }) {
            id
            name
            displayName
            service
          }
        }
        """,
        {"orgId": org_id},
    )
    return payload.get("data", {}).get("channels", []) or []


def resolve_x_channel_id() -> str:
    explicit = (os.getenv("BUFFER_CHANNEL_ID") or "").strip()
    if explicit:
        return explicit

    org_override = (os.getenv("BUFFER_ORGANIZATION_ID") or "").strip()
    orgs = _organizations()
    if org_override:
        orgs = [o for o in orgs if o.get("id") == org_override]
        if not orgs:
            raise BufferError("BUFFER_ORGANIZATION_ID does not match an accessible Buffer organization.")

    matches: List[Dict[str, str]] = []
    for org in orgs:
        for channel in _channels(str(org["id"])):
            if str(channel.get("service", "")).lower() == "twitter":
                matches.append(channel)

    if not matches:
        raise BufferError("No connected X/Twitter channel was found in Buffer.")
    if len(matches) > 1:
        summary = ", ".join(
            f"{c.get('displayName') or c.get('name') or '?'} ({c.get('id')})" for c in matches
        )
        raise BufferError(
            "Multiple X/Twitter channels were found. Set BUFFER_CHANNEL_ID to the intended channel. "
            f"Candidates: {summary}"
        )
    return str(matches[0]["id"])


def _create_post(input_data: Dict[str, Any]) -> str:
    payload = graphql(
        """
        mutation CreatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            __typename
            ... on PostActionSuccess {
              post { id status }
            }
            ... on MutationError {
              message
            }
          }
        }
        """,
        {"input": input_data},
    )
    result = payload.get("data", {}).get("createPost") or {}
    if result.get("__typename") != "PostActionSuccess":
        raise BufferError(f"Buffer createPost failed: {result.get('message') or result}")
    post = result.get("post") or {}
    post_id = str(post.get("id") or "")
    if not post_id:
        raise BufferError(f"Buffer createPost returned no post id: {result}")
    return post_id


def post_text(text: str, *, image_url: Optional[str] = None) -> str:
    channel_id = resolve_x_channel_id()
    data: Dict[str, Any] = {
        "text": _ensure_reelpal_tag(text),
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "assets": [],
    }
    if image_url:
        data["assets"] = [{"image": {"url": image_url}}]
    return _create_post(data)


def post_video(text: str, video_url: str) -> str:
    url = str(video_url or "").strip()
    if not url.startswith("https://"):
        raise BufferError("Video URL must be a public HTTPS URL.")
    channel_id = resolve_x_channel_id()
    return _create_post({
        "text": _ensure_reelpal_tag(text),
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "assets": [{"video": {"url": url}}],
    })


def post_thread(posts: Iterable[str]) -> str:
    texts = [_ensure_reelpal_tag(p) for p in posts if str(p).strip()]
    if not texts:
        raise BufferError("Thread is empty.")

    channel_id = resolve_x_channel_id()
    data: Dict[str, Any] = {
        "text": texts[0],
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "assets": [],
        "metadata": {
            "twitter": {
                "thread": [{"text": text} for text in texts],
            }
        },
    }
    return _create_post(data)


def post_video_thread(posts: Iterable[str], video_url: str) -> str:
    texts = [_ensure_reelpal_tag(p) for p in posts if str(p).strip()]
    if not texts:
        raise BufferError("Video thread is empty.")
    url = str(video_url or "").strip()
    if not url.startswith("https://"):
        raise BufferError("Video URL must be a public HTTPS URL.")

    channel_id = resolve_x_channel_id()
    data: Dict[str, Any] = {
        "text": texts[0],
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "assets": [{"video": {"url": url}}],
        "metadata": {
            "twitter": {
                "thread": [{"text": text} for text in texts],
            }
        },
    }
    return _create_post(data)
