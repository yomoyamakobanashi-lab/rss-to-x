import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from buffer_client import BufferError, _ensure_reelpal_tag
from scripts import social_pack_buffer, daily_content_buffer
from scripts.episode_links import x_length
from scripts.funny_clip_buffer import render_posts
from scripts.social_pack_autofill import build_items
from scripts.verify_funny_publication import verify


class LengthRecoveryTests(unittest.TestCase):
    def test_japanese_and_existing_tag_are_checked_before_submission(self):
        for text in ("書" * 140, "書" * 140 + "\n\n#リルパル"):
            with self.assertRaises(BufferError):
                _ensure_reelpal_tag(text)
        self.assertLessEqual(x_length(_ensure_reelpal_tag("書" * 132)), 280)

    def test_long_bilingual_title_and_chapters_fit_with_required_tag(self):
        latest = {
            "listen_episode_url": "https://listen.style/p/reelpal/test1234",
            "title": "映画作品のとても長い日本語題名" * 10 + " Bilingual title" * 10,
            "chapters": [{"title": f"議論{i}の非常に長い章題" * 12} for i in range(4)],
        }
        items = build_items(latest, datetime(2026, 10, 10, tzinfo=ZoneInfo("Asia/Tokyo")))
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertLessEqual(x_length(_ensure_reelpal_tag(item["text"])), 280)

    def test_bad_queue_entry_does_not_block_valid_entry(self):
        good = {"id": "good", "kind": "episode_hook", "text": "短い案内",
                "not_before": "2026-10-01T06:00:00+09:00"}
        bad = dict(good, id="bad", text="書" * 160)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            path.write_text(json.dumps([bad, good]))
            with patch.object(social_pack_buffer, "QUEUE_PATH", path):
                self.assertEqual(social_pack_buffer.load_queue(strict=False), [good])
                with self.assertRaises(BufferError):
                    social_pack_buffer.load_queue()

    def test_long_dialogue_keeps_whole_turns_in_order_across_thread(self):
        turns = [char * 50 for char in ("一", "二", "三")]
        item = {"id": "long", "dialogue": turns, "hook": "会話の続き", "text": "会話", "topic": "会話"}
        posts = render_posts(item)
        self.assertGreater(len(posts), 1)
        for post in posts:
            self.assertLessEqual(x_length(post), 280)
            self.assertIn("#リルパル", post)
        combined = "\n".join(posts)
        self.assertEqual([combined.index(turn) for turn in turns], sorted(combined.index(turn) for turn in turns))
        for turn in turns:
            self.assertIn(f"「{turn}」", combined)

    @patch.object(social_pack_buffer, "build_reply")
    def test_pending_episode_does_not_block_a_ready_item(self, reply):
        items = [{"id": "pending", "not_before": "2026-10-01T06:00:00+09:00"},
                 {"id": "ready", "not_before": "2026-10-02T06:00:00+09:00"}]
        reply.side_effect = [RuntimeError("unverified episode"), "verified reply"]
        state = {"posted_ids": []}
        item, text = social_pack_buffer.pick_ready(items, state, datetime(2026, 10, 10, tzinfo=ZoneInfo("Asia/Tokyo")))
        self.assertEqual(item["id"], "ready")
        self.assertEqual(state["posted_ids"], [])

    @patch.object(daily_content_buffer, "execute_slot")
    @patch.object(daily_content_buffer, "load_state")
    def test_selected_recovery_slot_honors_existing_daily_guard(self, load, execute):
        load.return_value = {"days": {"2026-10-10": {"posted_slots": ["funny"]}}}
        with patch("sys.argv", ["daily", "--slot", "funny", "--dry-run", "--now", "2026-10-10T20:00:00+09:00"]):
            self.assertEqual(daily_content_buffer.main(), 0)
        execute.assert_not_called()

    @patch("scripts.verify_funny_publication.graphql")
    def test_acceptance_is_not_mistaken_for_publication(self, graphql):
        graphql.side_effect = [{"data": {"post": {"status": "buffer"}}},
                               {"data": {"post": {"status": "sent", "externalLink": "https://x.com/reelpal/status/1"}}}]
        self.assertEqual(verify("abc", attempts=2, delay=0)["status"], "sent")

    @patch("scripts.verify_funny_publication.graphql")
    def test_publish_failure_is_reported_without_another_post(self, graphql):
        graphql.return_value = {"data": {"post": {"status": "error"}}}
        with self.assertRaises(BufferError):
            verify("abc", attempts=2, delay=0)
        self.assertEqual(graphql.call_count, 1)
