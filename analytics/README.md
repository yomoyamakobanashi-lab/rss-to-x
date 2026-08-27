# X impression analytics

`x-performance-analytics.yml` が毎日 Buffer API から送信済みX投稿の実績を取得する。

## 生成ファイル

- `x_metrics_history.json` — 投稿IDごとの最新メトリクス履歴
- `X_PERFORMANCE.md` — 直近28日の比較ダッシュボード

## 主指標

インプレッション最大化の判断では、バズ1本に平均値が引っ張られないよう **中央値imp** を主指標にする。

副指標:
- impressions
- engagementRate
- comments
- reposts
- reactions
- clicks

## 比較軸

- 投稿タイプ: quick_reply / discussion / new_podcast / new_note / archive / survey など
- 曜日
- 時間帯（JST）
- 投稿単位の上位ランキング

## 運用ルール

1. 1タイプ最低3投稿までは大きな結論を出さない。
2. 変更は一度に1要因だけにする（曜日・時刻・文型を同時に変えない）。
3. 新着告知は通常投稿と分けて評価する。
4. 外部リンクを含む送客投稿とネイティブ投稿を分けて評価する。
5. Bufferのメトリクス更新は最大約24時間遅れるため、公開当日の数値で勝敗を決めない。

このダッシュボードを基に、4〜6週間単位で投稿比率・時刻・文型を更新する。
