from __future__ import annotations

import json
import unittest

from scripts.episode_links import (
    CATALOG_PATH,
    render_episode_reply,
    resolve_episode_links,
    validate_catalog,
    x_length,
)
from scripts.refresh_episode_platform_links import validate_index_catalog_alignment


class EpisodePlatformLinkTests(unittest.TestCase):
    def test_catalog_has_complete_spotify_and_apple_coverage(self):
        counts = validate_catalog()
        self.assertGreaterEqual(counts["episodes"], 128)
        self.assertEqual(counts["spotify"], counts["episodes"])
        self.assertGreaterEqual(counts["apple"], 128)
        self.assertGreaterEqual(counts["youtube"], 126)

    def test_latest_episode_resolves_by_internal_listen_identity(self):
        links = resolve_episode_links(
            listen_url="https://listen.style/p/reelpal/0pzhzwyt"
        )
        self.assertEqual(
            links["spotify_url"],
            "https://open.spotify.com/episode/1kKJIgESabkQshd3sSwRLH",
        )
        self.assertEqual(
            links["apple_url"],
            "https://podcasts.apple.com/jp/podcast/id1810778208?i=1000787246039",
        )
        self.assertEqual(links["youtube_url"], "https://youtu.be/-GVTupJxerY")

    def test_reply_contains_verified_platforms_but_never_listen(self):
        reply = render_episode_reply(
            title="真夏のホラー企画 第一弾 映画『サユリ』",
            listen_url="https://listen.style/p/reelpal/aon5ynuf",
        )
        self.assertIn("open.spotify.com/episode/", reply)
        self.assertIn("podcasts.apple.com/jp/podcast/", reply)
        self.assertIn("youtu.be/", reply)
        self.assertNotIn("listen.style", reply)
        self.assertLessEqual(len(reply), 280)
        self.assertLessEqual(x_length(reply), 280)

    def test_missing_youtube_episode_omits_only_youtube(self):
        rows = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        row = next((item for item in rows if not item.get("youtube_url")), None)
        if row is None:
            self.skipTest("every episode is now available on YouTube")
        reply = render_episode_reply(guid=row["guid"])
        self.assertIn("Spotify", reply)
        self.assertIn("Apple Podcasts", reply)
        self.assertNotIn("YouTube", reply)

    def test_unknown_episode_fails_instead_of_using_a_fallback(self):
        with self.assertRaises(RuntimeError):
            resolve_episode_links(title="存在しない架空の回")

    def test_pending_new_episode_does_not_invalidate_verified_catalog(self):
        spotify = [
            {
                "guid": "resolved-guid",
                "title": "既存回",
                "spotifyUrl": "https://open.spotify.com/episode/Resolved123",
            },
            {
                "guid": "pending-guid",
                "title": "公開直後の新着回",
                "spotifyUrl": None,
            },
        ]
        catalog = [
            {
                "guid": "resolved-guid",
                "spotify_url": "https://open.spotify.com/episode/Resolved123",
            }
        ]
        alignment = validate_index_catalog_alignment(spotify, catalog)
        self.assertEqual(alignment["resolved_episodes"], 1)
        self.assertEqual(alignment["pending_episodes"], 1)
        self.assertEqual(alignment["catalog_episodes"], 1)

    def test_resolved_episode_missing_from_catalog_still_fails(self):
        spotify = [
            {
                "guid": "resolved-guid",
                "title": "解決済み新着回",
                "spotifyUrl": "https://open.spotify.com/episode/Resolved123",
            }
        ]
        with self.assertRaises(RuntimeError):
            validate_index_catalog_alignment(spotify, [])


if __name__ == "__main__":
    unittest.main()
