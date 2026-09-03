from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.daily_content_buffer import execute_slot, next_due_slot
from scripts.social_pack_autofill import build_items

JST = ZoneInfo("Asia/Tokyo")


class DailyContentScheduleTests(unittest.TestCase):
    def test_slots_become_due_in_order(self):
        empty = {"days": {}}
        self.assertEqual(
            next_due_slot(datetime(2026, 9, 3, 8, 15, tzinfo=JST), empty),
            "engagement",
        )

        after_engagement = {
            "days": {"2026-09-03": {"posted_slots": ["engagement"]}}
        }
        self.assertEqual(
            next_due_slot(datetime(2026, 9, 3, 17, 15, tzinfo=JST), after_engagement),
            "feature",
        )

        after_feature = {
            "days": {
                "2026-09-03": {
                    "posted_slots": ["engagement", "feature"],
                }
            }
        }
        self.assertEqual(
            next_due_slot(datetime(2026, 9, 3, 21, 25, tzinfo=JST), after_feature),
            "funny",
        )

    def test_completed_day_never_reposts(self):
        state = {
            "days": {
                "2026-09-03": {
                    "posted_slots": ["engagement", "feature", "funny"],
                }
            }
        }
        self.assertIsNone(
            next_due_slot(datetime(2026, 9, 3, 23, 59, tzinfo=JST), state)
        )

    @patch("scripts.daily_content_buffer._run_module")
    def test_engagement_falls_back_without_dropping_the_slot(self, run_module):
        run_module.side_effect = [(False, "bank exhausted"), (True, "accepted")]
        module = execute_slot("engagement", datetime(2026, 9, 3, 9, 0, tzinfo=JST))
        self.assertEqual(module, "scripts.quick_reply_buffer")
        self.assertEqual(
            [call.args[0] for call in run_module.call_args_list],
            ["scripts.discussion_buffer", "scripts.quick_reply_buffer"],
        )

    @patch("scripts.daily_content_buffer._run_module")
    def test_saturday_feature_prefers_pack_then_digest(self, run_module):
        run_module.side_effect = [(False, "no due pack"), (True, "accepted")]
        module = execute_slot("feature", datetime(2026, 9, 5, 18, 0, tzinfo=JST))
        self.assertEqual(module, "scripts.weekly_digest_buffer")
        self.assertEqual(
            [call.args[0] for call in run_module.call_args_list],
            ["scripts.social_pack_buffer", "scripts.weekly_digest_buffer"],
        )


class SocialPackAutofillTests(unittest.TestCase):
    def test_builds_two_short_grounded_items(self):
        latest = {
            "listen_episode_url": "https://listen.style/p/reelpal/example1",
            "title": "映画『テスト作品』を語る回",
            "chapters": [
                {"timestamp": "00:00", "title": "オープニング"},
                {"timestamp": "05:00", "title": "作品の第一印象"},
                {"timestamp": "15:00", "title": "登場人物について"},
                {"timestamp": "30:00", "title": "物語の背景と社会"},
                {"timestamp": "45:00", "title": "エンディング"},
            ],
        }
        items = build_items(latest, datetime(2026, 9, 3, 12, 0, tzinfo=JST))
        self.assertEqual(len(items), 2)
        self.assertEqual({item["kind"] for item in items}, {"three_hooks", "episode_hook"})
        for item in items:
            self.assertEqual(item["source_url"], latest["listen_episode_url"])
            self.assertLessEqual(len(item["text"] + "\n\n#リルパル"), 280)
            self.assertNotIn("オープニング", item["text"])
            self.assertNotIn("エンディング", item["text"])


if __name__ == "__main__":
    unittest.main()
