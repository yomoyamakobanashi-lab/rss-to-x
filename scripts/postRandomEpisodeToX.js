const fs = require('fs');
const path = require('path');
const { TwitterApi } = require('twitter-api-v2');

const DATA_DIR = 'data';
const PHRASES_FILE = path.join(DATA_DIR, 'phrases.txt');
const EPISODES_FILE = path.join(DATA_DIR, 'episodes.json');

function ensurePhrasesFile() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

  if (!fs.existsSync(PHRASES_FILE)) {
    const defaults = [
      '今日はこの回をどうぞ🎬',
      '過去回から一本🎧',
      'この回、今聴くと刺さるかも',
      'あらためておすすめしたい一本',
      'ちょっと時間あるならこの回',
      '今日はこれを流してみてほしい',
      '忘れた頃にこの回',
      '過去回ピックアップ🎬',
      '気分に合いそうな回',
      'ラジオ感覚でどうぞ',
      '作業のお供にこの回',
      '通勤通学のお供に',
      '気楽に聴ける回です',
      '今週の振り返りに',
      '今日は軽めにこの回',
      'たまには過去回',
      '今でも好きな回',
      'このテーマ、今こそ',
      '静かにおすすめ',
      '個人的推し回',
    ];
    fs.writeFileSync(PHRASES_FILE, defaults.join('\n'), 'utf8');
  }
}

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function readPhrases() {
  ensurePhrasesFile();
  return fs
    .readFileSync(PHRASES_FILE, 'utf8')
    .split(/\r?\n/)
    .map(s => s.trim())
    .filter(Boolean);
}

function readEpisodes() {
  if (!fs.existsSync(EPISODES_FILE)) {
    throw new Error(`Missing ${EPISODES_FILE}. buildEpisodesFromRSS.js may have failed.`);
  }
  const episodes = JSON.parse(fs.readFileSync(EPISODES_FILE, 'utf8'));
  if (!Array.isArray(episodes) || episodes.length === 0) {
    throw new Error(`No episodes found in ${EPISODES_FILE}.`);
  }
  return episodes;
}

(async () => {
  const phrases = readPhrases();
  const episodes = readEpisodes();

  const phrase = pickRandom(phrases);
  const episode = pickRandom(episodes);

  // Link is counted as ~23 chars by X; keep text short and stable.
  const text = `${phrase}\n${episode}\n#リルパル`;

  const client = new TwitterApi({
    appKey: process.env.X_API_KEY,
    appSecret: process.env.X_API_SECRET,
    accessToken: process.env.X_ACCESS_TOKEN,
    accessSecret: process.env.X_ACCESS_SECRET,
  });

  await client.v2.tweet(text);
  console.log('posted');
})();
