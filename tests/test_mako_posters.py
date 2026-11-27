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
        with patch.object(stories,"_BLOCKED_MEDIA_HOSTS",set()), patch.object(stories.requests,"get") as get:
            get.return_value.__enter__.return_value=response
            self.assertEqual(stories.reachable_poster(item()),item()["poster"]["image_url"])
            response.status_code=429
            self.assertIsNone(stories.reachable_poster(item()))

    def test_preflight_rejects_wrong_mime_and_oversize_stream(self):
        response=MagicMock()
        response.status_code=200
        with patch.object(stories,"_BLOCKED_MEDIA_HOSTS",set()), patch.object(stories.requests,"get") as get:
            get.return_value.__enter__.return_value=response
            response.headers={"Content-Type":"image/png"}
            response.iter_content.return_value=iter([bytes.fromhex("ffd8ff")])
            self.assertIsNone(stories.reachable_poster(item()))
            response.headers={"Content-Type":"image/jpeg"}
            response.iter_content.return_value=iter([bytes.fromhex("ffd8ff"), b"x" * (5 * 1024 * 1024)])
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

    def test_related_image_when_poster_pending(self):
        row=item()
        row["poster"]["status"]="pending"
        row["related_image"]={**row["poster"],"status":"rights_checked","asset_kind":"archival_photo",
                              "asset_title":"Historical photo","relation":"Illustrates the article"}
        self.assertEqual(stories.visual_assets(row),[("related_image",row["related_image"]["image_url"])])
        row["posts"]=["hook #まこの映画小話","body","end"]
        row["sources"]=[{"name":"source","url":"https://example.org"}]
        self.assertTrue(any("関連資料画像（映画ポスターではありません）" in p for p in stories.render(row,visual_field="related_image")))

    def test_poster_preferred_and_related_fallback(self):
        row=item()
        row["related_image"]={**row["poster"],"asset_kind":"archival_photo","asset_title":"Photo",
                              "relation":"Relevant","image_url":"https://upload.wikimedia.org/photo.jpg"}
        with patch.object(stories,"_reachable_image",side_effect=[None,row["related_image"]["image_url"]]):
            self.assertEqual(stories.reachable_visual(row),("related_image",row["related_image"]["image_url"]))

    def test_no_visual_holds_slot_without_reservation(self):
        now=datetime(2026,11,27,7,tzinfo=ZoneInfo("Asia/Tokyo"))
        state={"days":{},"used_story_ids":[]}
        row={"id":"missing","weekday":4}
        with patch.object(pub,"reserve_and_send") as send:
            self.assertIsNone(pub.due_visual_story(now,state,[row]))
            send.assert_not_called()
        self.assertEqual(state["days"][now.date().isoformat()],{})
        self.assertEqual(state["used_story_ids"],[])

    def test_visual_selection_skips_unavailable_and_preserves_weekday(self):
        now=datetime(2026,11,27,7,tzinfo=ZoneInfo("Asia/Tokyo"))
        state={"days":{},"used_story_ids":[]}
        missing={"id":"missing","weekday":4}
        other={"id":"other","weekday":5}
        ready={"id":"ready","weekday":4}
        with patch.object(stories,"reachable_visual",side_effect=[None,("related_image","https://upload.wikimedia.org/ready.jpg")]) as reach:
            self.assertEqual(pub.due_visual_story(now,state,[missing,other,ready]),(ready,"related_image","https://upload.wikimedia.org/ready.jpg"))
            self.assertEqual([a.args[0]["id"] for a in reach.call_args_list],["missing","ready"])

    def test_throttled_host_is_not_requested_again_in_run(self):
        response=MagicMock()
        response.status_code=429
        response.headers={"Retry-After":"1000"}
        row=item()
        with patch.object(stories,"_BLOCKED_MEDIA_HOSTS",set()), patch.object(stories.requests,"get") as get:
            get.return_value.__enter__.return_value=response
            self.assertIsNone(stories.reachable_poster(row))
            self.assertIsNone(stories.reachable_poster(row))
            get.assert_called_once()

    def test_institutional_host_and_lookalike(self):
        row=item()
        row["poster"]["image_url"]="https://collectionapi.metmuseum.org/api/collection/v1/iiif/123/456/main-image"
        self.assertEqual(stories.poster_url(row),row["poster"]["image_url"])
        row["poster"]["image_url"]="https://collectionapi.metmuseum.org.invalid.test/image"
        with self.assertRaises(ValueError):
            stories.poster_url(row)

if __name__=="__main__":
    unittest.main()
