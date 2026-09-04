from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import trend_episode_tiein_buffer as trend
from scripts import x_metrics_report


JST = ZoneInfo("Asia/Tokyo")


class TrendDiscoveryTests(unittest.TestCase):
    def test_auto_terms_keep_titles_and_drop_generic_tags(self):
        title = "#ホラー映画 映画『#グレムリン』『#グレムリン２』 #Netflix #監督"
        self.assertEqual(trend.extract_episode_terms(title), ["グレムリン", "グレムリン2"])

    def test_full_archive_becomes_discoverable(self):
        topics = trend.load_topics()
        automatic = [topic for topic in topics if topic.get("discovery_mode")]
        self.assertGreaterEqual(len(automatic), 90)
        self.assertTrue(all(topic["listen_url"].startswith("https://listen.style/p/reelpal/") for topic in automatic))

    def test_short_title_requires_boundaries(self):
        self.assertFalse(trend.contains_term(trend.norm("春のスプリング映画特集"), "リング"))
        self.assertTrue(trend.contains_term(trend.norm("映画『リング』再上映"), "リング"))

    def test_gap_guard_protects_baseline_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.object(trend, "DAILY_STATE_PATH", missing):
                self.assertFalse(trend.too_close_to_daily_content(datetime(2026, 9, 3, 15, 35, tzinfo=JST)))
                self.assertTrue(trend.too_close_to_daily_content(datetime(2026, 9, 3, 16, 20, tzinfo=JST)))

    def test_discovery_root_is_link_free_and_episode_is_in_reply(self):
        candidate = {
            "key": "12345678abcdef",
            "headline": "グレムリン最新作の予告が公開",
            "source": "映画.com",
            "topic": {
                "episode_title": "真夏のホラー企画 第一弾 映画『サユリ』",
                "listen_url": "https://listen.style/p/reelpal/aon5ynuf",
                "angle": "",
                "discovery_mode": True,
            },
        }
        root, reply = trend.compose(candidate)
        self.assertTrue(root.startswith("映画好きに聞きたい。"))
        self.assertNotIn("http", root)
        self.assertIn("https://open.spotify.com/episode/", reply)
        self.assertIn("https://podcasts.apple.com/jp/podcast/", reply)
        self.assertIn("https://youtu.be/", reply)
        self.assertNotIn("listen.style", reply)
        self.assertLessEqual(len(root), trend.MAX_ROOT_LEN)
        self.assertLessEqual(len(reply), 280)

    @patch("scripts.trend_episode_tiein_buffer.fetch_feed")
    def test_broad_feed_matches_an_automatic_archive_topic(self, fetch_feed):
        fetch_feed.return_value = [{
            "id": "news-1",
            "title": "映画『グレムリン』最新作の予告が公開 - 映画.com",
            "source": {"title": "映画.com"},
            "published_parsed": datetime.now(timezone.utc).timetuple(),
            "summary": "映画シリーズの最新予告",
            "link": "https://example.com/news-1",
        }]
        topic = {
            "episode_title": "グレムリン",
            "listen_url": "https://listen.style/p/reelpal/example",
            "search_queries": [],
            "exact_terms": ["グレムリン"],
            "related_terms": [],
            "context_terms": ["映画", "公開", "予告"],
            "angle": "",
            "discovery_mode": True,
        }
        candidates = trend.collect_candidates([topic], {
            "seen_news": [],
            "episode_last_posted": {},
        })
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["topic"]["episode_title"], "グレムリン")

    def test_metrics_table_exposes_click_proxy(self):
        rows = x_metrics_report.report_rows(
            [{"metrics": {"impressions": 100, "engagementRate": 2, "clicks": 4}}],
            lambda _: "discovery",
        )
        table = x_metrics_report.markdown_table(rows, "タイプ")
        self.assertIn("クリック", table)
        self.assertIn("4.00%", table)


if __name__ == "__main__":
    unittest.main()
