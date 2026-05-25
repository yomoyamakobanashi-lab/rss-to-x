const fs = require('fs');
const fetch = require('node-fetch');
const cheerio = require('cheerio');

const LISTEN_URL = 'https://listen.style/p/reelpal';
const FORM_URL = 'https://forms.gle/4PT2GBA7TY8vAoCx7';

const OUTPUT_DIR = 'data';
const SPOTIFY_FILE = 'data/spotify_episodes.json';
const OUTPUT_FILE = 'data/thread_drafts.json';

const MAX_DRAFTS = 20;
const MAX_PARENT_LENGTH = 220;

function normalizeTitle(title) {
  return String(title || '')
    .replace(/[#＃]/g, '')
    .replace(/[【】「」『』"“”'’]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function cleanText(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .trim();
}

function normalizeHtmlText(text) {
  return String(text || '')
    .replace(/\\u002F/g, '/')
    .replace(/\\\//g, '/')
    .replace(/&amp;/g, '&')
    .replace(/&#x2F;/g, '/')
    .replace(/&#47;/g, '/');
}

function findSpotifyEpisodeUrlFromHtml(html) {
  const normalized = normalizeHtmlText(html);
  const directMatch = normalized.match(/https:\/\/open\.spotify\.com\/episode\/[A-Za-z0-9]+/);
  if (directMatch) return directMatch[0];

  const spotifyUriMatch = normalized.match(/spotify:episode:([A-Za-z0-9]+)/);
  if (spotifyUriMatch) {
    return `https://open.spotify.com/episode/${spotifyUriMatch[1]}`;
  }

  return null;
}

function truncate(text, max) {
  const t = cleanText(text);
  if (t.length <= max) return t;
  return t.slice(0, max - 1).trim() + '…';
}

function titleSimilarity(a, b) {
  const aa = normalizeTitle(a);
  const bb = normalizeTitle(b);

  if (!aa || !bb) return 0;
  if (aa === bb) return 1;
  if (aa.includes(bb) || bb.includes(aa)) return 0.85;

  const aTokens = new Set(aa.split(/[ \-_/・、。〜｜|]+/).filter(t => t.length >= 2));
  const bTokens = new Set(bb.split(/[ \-_/・、。〜｜|]+/).filter(t => t.length >= 2));

  if (aTokens.size === 0 || bTokens.size === 0) return 0;

  let hit = 0;
  for (const token of aTokens) {
    if (bTokens.has(token)) hit++;
  }

  return hit / Math.max(aTokens.size, bTokens.size);
}

function extractEpisodeLinksFromIndex(html) {
  const $ = cheerio.load(html);
  const urls = [];

  $('a[href]').each((_, el) => {
    const href = $(el).attr('href');
    if (!href) return;

    let url = null;

    if (href.startsWith('/p/reelpal/')) {
      url = `https://listen.style${href.split('?')[0].split('#')[0]}`;
    }

    if (href.startsWith('https://listen.style/p/reelpal/')) {
      url = href.split('?')[0].split('#')[0];
    }

    if (!url) return;
    if (url === LISTEN_URL) return;

    const slug = url.replace('https://listen.style/p/reelpal/', '').trim();
    if (!slug) return;
    if (slug.includes('/')) return;

    urls.push(url);
  });

  return [...new Set(urls)];
}

function extractTitle($) {
  const h1 = cleanText($('h1').first().text());
  if (h1 && h1.length > 3) return h1;

  const title = cleanText($('title').first().text());
  return title.replace(/- LISTEN.*$/i, '').trim();
}

function extractUsefulText($) {
  const parts = [];

  $('h1, h2, h3, p, li, article, section, div').each((_, el) => {
    const text = cleanText($(el).text());
    if (!text) return;
    if (text.length < 20) return;

    const ngWords = [
      'LISTEN',
      'Copy Link',
      'Share',
      'Play',
      'Pause',
      'Color Theme',
      'Apple Podcast',
      'Spotify',
      'RSS',
      'ログイン',
      '新規登録'
    ];

    if (ngWords.some(w => text.includes(w))) return;

    parts.push(text);
  });

  return [...new Set(parts)].join(' ');
}

function extractWorkName(title) {
  const patterns = [
    /『([^』]+)』/,
    /「([^」]+)」/,
    /#([A-Za-z0-9ぁ-んァ-ヶ一-龠ー・：:！!？?]+)/,
    /映画\s*([^〜｜|]+)/,
  ];

  for (const p of patterns) {
    const m = title.match(p);
    if (m && m[1]) {
      return cleanText(m[1]).slice(0, 32);
    }
  }

  return truncate(title, 32);
}

function pickKeywords(text) {
  const candidates = [
    '罪悪感', '搾取', '消費', '家族', '記憶', '宗教', '信仰', '暴力',
    '社会', '歴史', '階級', '差別', '教育', '倫理', '神話', '都市伝説',
    'ノスタルジー', '資本主義', 'フェミニズム', '家父長制', '植民地主義',
    '身体', '恐怖', '怪異', '呪い', '孤独', '成長', '喪失', '欲望',
    '自由', '選択', '責任', '友情', '愛', '死', '正義', '映画', '文化',
    'コメディ', 'ホラー', 'ファンタジー', 'アクション', 'ドラマ'
  ];

  return candidates.filter(k => text.includes(k)).slice(0, 3);
}

function buildParentDraft(title, body) {
  const work = extractWorkName(title);
  const keywords = pickKeywords(`${title} ${body}`);

  const k1 = keywords[0] || '作品の奥にある違和感';
  const k2 = keywords[1] || '観終わったあとに残る感触';

  const templates = [
    `『${work}』、ただの作品紹介で済ませるには少し厄介です。\n\n今回は、${k1}と${k2}のあいだに残る手触りを掘っています。\n#リルパル`,

    `この映画、面白い／怖いで片づける前に、少し立ち止まりたくなる作品です。\n\n『${work}』を、${k1}という視点から話しています。\n#リルパル`,

    `『${work}』を観て残るのは、物語の筋よりも「なぜそれが引っかかるのか」という感覚かもしれません。\n\n今回はそのあたりを話しています。\n#リルパル`,

    `あなたは『${work}』を、どんな映画として観ましたか。\n\n今回は、${k1}や${k2}を手がかりに、作品の見え方を少し掘り下げています。\n#リルパル`,

    `『${work}』、油断すると娯楽の顔をしたまま、現実の嫌な部分をすっと差し出してくるタイプの作品です。\n\n今回はそのへんを語っています。\n#リルパル`,

    `今回は『${work}』を入口に、映画の中にある${k1}について話しています。\n\n観た人の感想も聞きたい一本です。\n#リルパル`
  ];

  let draft = templates[Math.floor(Math.random() * templates.length)];

  if (draft.length <= MAX_PARENT_LENGTH) return draft;

  draft = `『${work}』を、ただの作品紹介ではなく、${k1}という視点から話しています。\n\n観た人の感想も聞きたい回です。\n#リルパル`;

  if (draft.length <= MAX_PARENT_LENGTH) return draft;

  return `『${work}』回。\n\n作品の奥に残る違和感を、少し掘り下げて話しています。\n#リルパル`;
}

function pickSpotifyMatch(listenTitle, spotifyEpisodes) {
  const available = spotifyEpisodes.filter(ep => ep.spotifyUrl);

  let best = null;
  let bestScore = 0;

  for (const ep of available) {
    const score = titleSimilarity(listenTitle, ep.title);
    if (score > bestScore) {
      best = ep;
      bestScore = score;
    }
  }

  if (best && bestScore >= 0.12) {
    return {
      spotifyUrl: best.spotifyUrl,
      matchedSpotifyTitle: best.title,
      matchScore: bestScore,
      matchMethod: 'rss-title'
    };
  }

  return null;
}

async function fetchText(url) {
  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 GitHubActions ReelPalBot/1.0'
    }
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }

  return res.text();
}

(async () => {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  let spotifyEpisodes = [];

  if (fs.existsSync(SPOTIFY_FILE)) {
    spotifyEpisodes = JSON.parse(fs.readFileSync(SPOTIFY_FILE, 'utf8'));
  }

  console.log(`RSS episodes: ${spotifyEpisodes.length}`);
  console.log(`RSS episodes with Spotify URL: ${spotifyEpisodes.filter(ep => ep.spotifyUrl).length}`);

  const indexHtml = await fetchText(LISTEN_URL);
  const episodeUrls = extractEpisodeLinksFromIndex(indexHtml).slice(0, MAX_DRAFTS);

  console.log(`LISTEN episode URLs found: ${episodeUrls.length}`);

  const drafts = [];

  for (let i = 0; i < episodeUrls.length; i++) {
    const listenUrl = episodeUrls[i];

    try {
      const html = await fetchText(listenUrl);
      const $ = cheerio.load(html);

      const title = extractTitle($);
      const body = extractUsefulText($);

      const spotifyFromListen = findSpotifyEpisodeUrlFromHtml(html);
      const rssMatch = pickSpotifyMatch(title, spotifyEpisodes);

      const spotifyUrl = spotifyFromListen || rssMatch?.spotifyUrl || null;
      const matchMethod = spotifyFromListen ? 'listen-html' : (rssMatch?.matchMethod || 'none');
      const matchedSpotifyTitle = rssMatch?.matchedSpotifyTitle || null;
      const matchScore = rssMatch?.matchScore || 0;

      console.log(`Checking: ${title}`);
      console.log(`Body length: ${body.length}`);
      console.log(`Spotify URL: ${spotifyUrl ? 'found' : 'none'} / ${matchMethod}`);

      if (!title || !spotifyUrl) {
        console.log(`Skipped: missing title or Spotify URL`);
        continue;
      }

      const usableBody = body || title;
      const parent = buildParentDraft(title, usableBody);

      const reply1 = `本編はこちら👇\n${spotifyUrl}\n#リルパル`;
      const reply2 = `感想・映画リクエストはこちら👇\n${FORM_URL}\n#リルパル`;

      drafts.push({
        title,
        parent,
        reply1,
        reply2,
        listenUrl,
        spotifyUrl,
        matchedSpotifyTitle,
        matchScore: Number(matchScore.toFixed(3)),
        matchMethod,
        sourceTextSample: truncate(usableBody, 260)
      });
    } catch (err) {
      console.error(`Skipped ${listenUrl}: ${err.message}`);
    }
  }

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(drafts, null, 2), 'utf8');
  console.log(`Generated ${drafts.length} thread drafts to ${OUTPUT_FILE}`);
})();
