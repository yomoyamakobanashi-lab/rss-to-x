#!/usr/bin/env python3
import json
from pathlib import Path

path = Path('data/funny_clip_posts.json')
data = json.loads(path.read_text(encoding='utf-8'))
existing = {str(x.get('id')) for x in data if isinstance(x, dict)}
items = [
    {
        'id': 'aetobodm-chaos-theory',
        'source': 'intermission-aetobodm',
        'episode_title': '〖インターミッション〗　 #一ノ瀬ワタル とか#アンジェリーナ・ジョリー とか#ウィキッド とか',
        'source_url': 'https://listen.style/p/reelpal/aetobodm',
        'timestamp': '15:04',
        'topic': 'カオス理論の人',
        'text': 'ジェフ・ゴールドブラムの話になると、作品名より先に「カオス理論の人」が出てくる二人。「いつちょっとカオスって言うかな」「水垂らさないの？」まで行き、俳優名より役の一場面の記憶が強すぎます。'
    },
    {
        'id': 'aetobodm-kingdom-memory',
        'source': 'intermission-aetobodm',
        'episode_title': '〖インターミッション〗　 #一ノ瀬ワタル とか#アンジェリーナ・ジョリー とか#ウィキッド とか',
        'source_url': 'https://listen.style/p/reelpal/aetobodm',
        'timestamp': '09:00',
        'topic': 'キングダムの記憶',
        'text': '『キングダム』の登場人物を思い出そうとして、「トントンの人」「フクロウの子」「八尺様みたいな声の人」と固有名詞がほぼ消滅。最後は「あいつ死んだ？」「生きてる」を何往復もして、記憶だけで作品を語る危険性が露呈します。'
    }
]
added = 0
for item in items:
    if item['id'] not in existing:
        data.append(item)
        added += 1
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'added={added}')
