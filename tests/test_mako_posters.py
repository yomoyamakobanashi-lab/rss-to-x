import unittest
from unittest.mock import MagicMock, patch

import buffer_client
from scripts import mako_film_stories as stories
from scripts import mako_daily_publisher as pub
from datetime import datetime
from zoneinfo import ZoneInfo

def item():
    return {"id":"test","poster":{
        "status":"rights_checked","film_title":"Test film","year":1920,
        "source_url":"https://commons.wikimedia.org/wiki/File:Test.jpg",
        "image_url":"https://upload.wikimedia.org/test.jpg",
        "license":"public_domain","rights_evidence_url":"https://commons.wikimedia.org/wiki/File:Test.jpg",
        "rights_basis":"Mock test evidence only","credit":"Mock test","checked_on":"2026-11-27"}}

class MakoPosterTests(unittest.TestCase):
    def test_pending_poster_never_fetches(self):
        row=item()
        row["poster"]["status"]="pending"
        with patch.object(stories.requests,"get") as get:
            self.assertIsNone(stories.reachable_poster(row))
            get.assert_not_called()

    def test_incomplete_license_rejected(self):
        row=item()
        del row["poster"]["rights_basis"]
        with self.assertRaises(ValueError):
            stories.poster_url(row)

    def test_untrusted_host_rejected(self):
        row=item()
        row["poster"]["image_url"]="https://upload.wikimedia.org.evil.test/image.jpg"
        with self.assertRaises(ValueError):
            stories.poster_url(row)

    def test_image_fetch_preflight(self):
        response=MagicMock()
        response.status_code=200
        response.headers={"Content-Type":"image/jpeg","Content-Length":"100"}
        response.iter_content.return_value=iter([bytes.fromhex("ffd8ff")])
        with patch.object(stories.requests,"get") as get:
            get.return_value.__enter__.return_value=response
            self.assertEqual(stories.reachable_poster(item()),item()["poster"]["image_url"])
            response.status_code=429
            self.assertIsNone(stories.reachable_poster(item()))

    def test_root_image_only(self):
        url=item()["poster"]["image_url"]
        with patch.object(buffer_client,"resolve_x_channel_id",return_value="channel"),patch.object(buffer_client,"_create_post",return_value="id") as create:
            self.assertEqual(buffer_client.post_thread(["hook","reply"],image_url=url),"id")
            payload=create.call_args.args[0]
            self.assertEqual(payload["assets"],[{"image":{"url":url}}])
            thread=payload["metadata"]["twitter"]["thread"]
            self.assertEqual(thread[0]["assets"],payload["assets"])
            self.assertNotIn("assets",thread[1])
            self.assertEqual(len(thread),2)
            buffer_client.post_thread(["hook","reply"])
            self.assertEqual(create.call_args.args[0]["assets"],[])

    def test_image_still_uses_single_reserved_root(self):
        date=datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
        state={"days":{},"used_story_ids":[]}
        url=item()["poster"]["image_url"]
        with patch.object(pub,"persist_reservation"),patch.object(pub,"write_ledger"),patch.object(pub,"post_thread",return_value="id") as send:
            pub.reserve_and_send(state,date,"story","story","test",["hook","reply"],image_url=url)
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["image_url"],url)
            self.assertEqual(len(state["days"][date]),1)

if __name__=="__main__":
    unittest.main()
