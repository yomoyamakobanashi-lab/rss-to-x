from __future__ import annotations

import unittest

from scripts.funny_clip_auto_ingest import (
    TranscriptTurn,
    extract_transcript_turns,
    materialize_clips,
    prioritize_episode_urls,
    transcript_health,
)
from scripts.funny_clip_buffer import pick_clip, render_root, rollover_state
from scripts.funny_clip_topology import validate_dynamic_topology


def bank_item(index: int, *, parent_id: str = "") -> dict:
    item = {
        "id": f"clip-{index}",
        "source": f"episode-{index}",
        "source_url": f"https://listen.style/p/reelpal/episode{index:03d}",
        "topic": f"topic-{index}",
    }
    if parent_id:
        item["parent_id"] = parent_id
    return item


class DynamicTopologyTests(unittest.TestCase):
    def test_new_base_episode_can_extend_the_original_127(self):
        bank = [bank_item(i) for i in range(128)]
        counts = validate_dynamic_topology(bank)
        self.assertEqual(counts["base_episodes"], 128)
        self.assertEqual(counts["clips"], 128)

    def test_duplicate_base_episode_is_rejected(self):
        bank = [bank_item(i) for i in range(127)]
        duplicate = dict(bank[-1], id="duplicate")
        with self.assertRaises(RuntimeError):
            validate_dynamic_topology(bank + [duplicate])


class RotationCycleTests(unittest.TestCase):
    def test_exhausted_bank_starts_new_cycle_and_preserves_recent_windows(self):
        bank = [bank_item(0), bank_item(1), bank_item(2)]
        state = {
            "used_ids": ["clip-0", "clip-1", "clip-2"],
            "recent_sources": ["episode-2"],
            "recent_topics": ["topic-2"],
            "cycle": 4,
        }
        prepared, rolled = rollover_state(bank, state)
        self.assertTrue(rolled)
        self.assertEqual(prepared["used_ids"], [])
        self.assertEqual(prepared["cycle"], 5)
        self.assertEqual(pick_clip(bank, prepared)["id"], "clip-0")

    def test_newly_added_clip_is_used_before_cycle_rollover(self):
        bank = [bank_item(0), bank_item(1), bank_item(2)]
        state = {
            "used_ids": ["clip-0", "clip-1"],
            "recent_sources": [],
            "recent_topics": [],
            "cycle": 1,
        }
        prepared, rolled = rollover_state(bank, state)
        self.assertFalse(rolled)
        self.assertEqual(pick_clip(bank, prepared)["id"], "clip-2")


class AutoIngestGateTests(unittest.TestCase):
    def test_never_seen_episodes_are_prioritized_over_due_retries(self):
        urls = ["retry-newer", "new-unseen", "retry-older"]
        state = {"episodes": {"retry-newer": {}, "retry-older": {}}}
        self.assertEqual(
            prioritize_episode_urls(urls, state),
            ["new-unseen", "retry-newer", "retry-older"],
        )

    def test_transcript_dom_becomes_stable_exact_turn_ids(self):
        page = """
        <html><body><h1>新着テスト回</h1>
        <div data-segment-index="7">最初の発言です。 次の発言ですか？ そうです。</div>
        <div data-segment-index="8">最後の発言です！</div>
        </body></html>
        """
        title, turns = extract_transcript_turns(page)
        self.assertEqual(title, "新着テスト回")
        self.assertEqual([turn.turn_id for turn in turns], ["T0000", "T0001", "T0002", "T0003"])
        self.assertEqual(turns[0].text, "最初の発言です。")
        self.assertEqual(turns[-1].segment_index, 8)

    def test_repetitive_asr_is_quarantined(self):
        turns = [
            TranscriptTurn(f"T{i:04d}", i, i, "おつかれさまでしたという同じ文章です。")
            for i in range(100)
        ]
        status, _ = transcript_health(turns)
        self.assertEqual(status, "quarantined_asr")

    def test_only_selected_transcript_text_materializes(self):
        turns = [
            TranscriptTurn("T0000", 0, 1, "スーパー銭湯の椅子が好きです。"),
            TranscriptTurn("T0001", 1, 1, "椅子ってどこでもあるじゃん。"),
            TranscriptTurn("T0002", 2, 2, "違うよ、椅子のクオリティだよ。"),
            TranscriptTurn("T0003", 3, 2, "そこまで力強く否定するの。"),
        ]
        items = materialize_clips(
            payload={
                "clips": [
                    {
                        "turn_ids": ["T0000", "T0001", "T0002", "T0003"],
                        "hook": "スーパー銭湯は椅子のクオリティで決まる。",
                    }
                ]
            },
            turns=turns,
            episode_title_value="新着テスト回",
            source_url="https://listen.style/p/reelpal/newtest1",
            spotify_url="https://open.spotify.com/episode/Verified123",
            existing_bank=[],
            model="test-model",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["dialogue"], [turn.text for turn in turns])
        self.assertTrue(items[0]["auto_generated"])

    def test_renderer_trimming_is_persisted_without_hidden_omissions(self):
        turns = [
            TranscriptTurn(
                f"T{i:04d}", i, i // 2, f"実在する長い発言{i}です" + ("あ" * 35) + "。"
            )
            for i in range(5)
        ]
        items = materialize_clips(
            payload={"clips": [{"turn_ids": [turn.turn_id for turn in turns]}]},
            turns=turns,
            episode_title_value="長文テスト回",
            source_url="https://listen.style/p/reelpal/longtest1",
            spotify_url="https://open.spotify.com/episode/Verified789",
            existing_bank=[],
            model="test-model",
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertGreaterEqual(len(item["dialogue"]), 3)
        self.assertLess(len(item["dialogue"]), len(turns))
        self.assertEqual(
            len(item["dialogue"]), len(item["transcript_proof"]["turn_ids"])
        )
        self.assertEqual(len(render_root(item).split("\n\n")) - 2, len(item["dialogue"]))

    def test_unknown_or_reordered_turn_ids_are_rejected(self):
        turns = [
            TranscriptTurn(f"T{i:04d}", i, i, f"実在する発言{i}です。")
            for i in range(4)
        ]
        common = {
            "turns": turns,
            "episode_title_value": "新着テスト回",
            "source_url": "https://listen.style/p/reelpal/newtest2",
            "spotify_url": "https://open.spotify.com/episode/Verified456",
            "existing_bank": [],
            "model": "test-model",
        }
        self.assertEqual(
            materialize_clips(
                payload={"clips": [{"turn_ids": ["T0000", "T9999", "T0002"]}]},
                **common,
            ),
            [],
        )
        self.assertEqual(
            materialize_clips(
                payload={"clips": [{"turn_ids": ["T0002", "T0001", "T0000"]}]},
                **common,
            ),
            [],
        )
        self.assertEqual(
            materialize_clips(
                payload={"clips": [{"turn_ids": ["T0000", "T0002", "T0003"]}]},
                **common,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
