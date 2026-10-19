import json
import unittest
from pathlib import Path

from scripts.episode_links import resolve_episode_links, render_episode_reply

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "data/quick_reply_posts.json",
    "data/discussion_posts.json",
    "data/discussion_posts_extra.json",
]

class EngagementEpisodeThreadTests(unittest.TestCase):
    def test_every_film_engagement_post_has_a_verified_guid(self):
        for filename in FILES:
            posts = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            self.assertTrue(posts, filename)
            for index, post in enumerate(posts):
                with self.subTest(file=filename, index=index):
                    guid = post.get("episode_guid")
                    self.assertTrue(guid, "missing episode_guid")
                    links = resolve_episode_links(guid=guid)
                    self.assertEqual(links["guid"], guid)
                    self.assertTrue(links["spotify_url"].startswith("https://open.spotify.com/episode/"))
                    reply = render_episode_reply(guid=guid)
                    self.assertIn(links["spotify_url"], reply)
                    self.assertIn("#リルパル", reply)

    def test_fireworks_post_has_correct_spotify_link(self):
        posts = json.loads((ROOT / "data/discussion_posts_extra.json").read_text(encoding="utf-8"))
        item = next(p for p in posts if p["text"].startswith("『打ち上げ花火、下から見るか？横から見るか？』って"))
        reply = render_episode_reply(guid=item["episode_guid"])
        self.assertIn("https://open.spotify.com/episode/2BGbUFeT6ItMcNWLJJDwQd", reply)

if __name__ == "__main__":
    unittest.main()
