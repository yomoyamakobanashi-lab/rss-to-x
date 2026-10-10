"""Read-only publication check; never retries createPost after an acceptance."""
from __future__ import annotations

import json
import time
from pathlib import Path

from buffer_client import graphql, BufferError

STATE_PATH = Path(__file__).resolve().parents[1] / "state_funny_clip.json"


def verify(post_id: str, attempts: int = 12, delay: float = 10) -> dict:
    # Use JSON quoting for the scalar ID, without interpolating arbitrary query text.
    query = ("query VerifyPublication { post(input: { id: " + json.dumps(post_id)
             + " }) { id status externalLink } }")
    for attempt in range(attempts):
        post = graphql(query).get("data", {}).get("post") or {}
        status = str(post.get("status") or "").lower()
        print(f"[INFO] publication id={post_id}; status={status}", flush=True)
        if status == "sent" and post.get("externalLink"):
            print(f"[OK] X publication confirmed: {post['externalLink']}", flush=True)
            return post
        if status == "error":
            raise BufferError(f"Buffer accepted but publishing failed for {post_id}")
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise BufferError(f"Publication still unconfirmed for {post_id}; do not repost")


if __name__ == "__main__":
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    post_id = state.get("last_buffer_post_id")
    if not post_id:
        raise BufferError("No accepted funny post ID is recorded")
    verify(str(post_id))
