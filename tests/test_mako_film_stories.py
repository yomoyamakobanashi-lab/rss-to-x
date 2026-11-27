import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import mako_film_stories as bank
from scripts import mako_daily_publisher as pub
from scripts.episode_links import x_length

JST=ZoneInfo("Asia/Tokyo")

class MakoStoriesTests(unittest.TestCase):
    def test_bank_and_replies(self):
        rows=bank.load_bank()
        self.assertGreaterEqual(len(rows),7)
        for r in rows:
            with self.subTest(id=r["id"]):
                parts=bank.render(r)
                self.assertTrue(all(x_length(t)<=280 and len(t)<=280 for t in parts))
                self.assertNotIn("http",parts[0])
                self.assertIn("https://open.spotify.com/",parts[-1])
                self.assertIn("#まこの映画小話",parts[0])
                for ref in r["sources"]:
                    self.assertTrue(any(ref["url"] in t for t in parts[1:-1]))

    def test_weekday_and_used_filter(self):
        rows=bank.load_bank()
        state={"days":{},"used_story_ids":[]}
        date=datetime(2026,11,27,7,tzinfo=JST)
        r=pub.due_story(date,state,rows)
        self.assertEqual(r["weekday"],4)
        state["days"][date.date().isoformat()]={"story":{"status":"reserved"}}
        self.assertIsNone(pub.due_story(date,state,rows))
        self.assertIsNone(pub.due_story(date.replace(hour=6),{"days":{},"used_story_ids":[]},rows))

    def test_reserve_before_write_and_no_duplicate(self):
        now=datetime.now(JST); date=now.date().isoformat()
        state={"days":{},"used_story_ids":[]}
        events=[]
        with patch.object(pub,"persist_reservation",side_effect=lambda _:events.append("durable")), patch.object(pub,"write_ledger"), patch.object(pub,"post_thread",side_effect=lambda _:events.append("send") or "buffer-id"):
            pub.reserve_and_send(state,date,"story","story","test-id",["test"])
            pub.reserve_and_send(state,date,"companion","funny","funny:1:test",["test"])
            self.assertEqual(events,["durable","send","durable","send"])
            with self.assertRaises(RuntimeError):
                pub.reserve_and_send(state,date,"companion","podcast","test-other",["test"])
            self.assertEqual(len(events),4)

    def test_failed_reservation_prevents_send(self):
        state={"days":{},"used_story_ids":[]}
        date=datetime.now(JST).date().isoformat()
        with patch.object(pub,"persist_reservation",side_effect=RuntimeError("push rejected")), patch.object(pub,"post_thread") as send:
            with self.assertRaises(RuntimeError):
                pub.reserve_and_send(state,date,"story","story","test",["test"])
            send.assert_not_called()

    def test_ambiguous_submission_never_retried(self):
        state={"days":{},"used_story_ids":[]}
        date=datetime.now(JST).date().isoformat()
        with patch.object(pub,"persist_reservation"), patch.object(pub,"post_thread",side_effect=TimeoutError("unknown")) as send:
            with self.assertRaises(TimeoutError):
                pub.reserve_and_send(state,date,"story","story","test",["test"])
            with self.assertRaises(RuntimeError):
                pub.reserve_and_send(state,date,"story","story","test",["test"])
            self.assertEqual(send.call_count,1)
            self.assertEqual(state["days"][date]["story"]["status"],"reserved")

if __name__=="__main__":
    unittest.main()
