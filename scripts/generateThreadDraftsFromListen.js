const fs = require('fs');
const fetch = require('node-fetch');
const cheerio = require('cheerio');

const LISTEN_URL = 'https://listen.style/p/reelpal';
const FORM_URL = 'https://forms.gle/4PT2GBA7TY8vAoCx7';

const OUTPUT_DIR = 'data';
const PLATFORM_FILE = 'data/episode_platform_links.json';
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

function truncate(text, max) {
  const t = cleanText(text);
  if (t.length <= max) return t;
  return t.slice(0, max - 1).trim() + '…';
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

function pickVerifiedPlatformMatch(listenUrl, listenTitle, platformEpisodes) {
  const direct = platformEpisodes.filter(ep =>
    Array.isArray(ep.listen_urls) && ep.listen_urls.includes(listenUrl)
  );
  if (direct.length === 1) return direct[0];

  const wanted = normalizeTitle(listenTitle);
  const exact = platformEpisodes.filter(ep => normalizeTitle(ep.title) === wanted);
  if (exact.length === 1) return exact[0];

  const contained = platformEpisodes.filter(ep => {
    const candidate = normalizeTitle(ep.title);
    return Math.min(wanted.length, candidate.length) >= 18 &&
      (wanted.startsWith(candidate) || candidate.startsWith(wanted));
  });
  return contained.length === 1 ? contained[0] : null;
}

function buildListeningReply(platform) {
  const lines = ['🎧 この回を聴く', 'Spotify', platform.spotify_url];
  if (platform.apple_url) lines.push('Apple Podcasts', platform.apple_url);
  if (platform.youtube_url) lines.push('YouTube', platform.youtube_url);
  lines.push('', '#リルパル');
  const reply = lines.join('\n');
  if (reply.length > 280 || reply.includes('listen.style')) {
    throw new Error('verified multi-platform reply is invalid');
  }
  return reply;
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

  let platformEpisodes = [];

  if (fs.existsSync(PLATFORM_FILE)) {
    platformEpisodes = JSON.parse(fs.readFileSync(PLATFORM_FILE, 'utf8'));
  }
  console.log(`Verified platform episodes: ${platformEpisodes.length}`);

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

      const platform = pickVerifiedPlatformMatch(listenUrl, title, platformEpisodes);
      const spotifyUrl = platform?.spotify_url || null;
      const matchMethod = platform ? 'verified-platform-catalog' : 'none';
      const matchedSpotifyTitle = platform?.title || null;
      const matchScore = platform ? 1 : 0;

      console.log(`Checking: ${title}`);
      console.log(`Body length: ${body.length}`);
      console.log(`Spotify URL: ${spotifyUrl ? 'found' : 'none'} / ${matchMethod}`);

      if (!title || !spotifyUrl) {
        console.log(`Skipped: missing title or Spotify URL`);
        continue;
      }

      const usableBody = body || title;
      const parent = buildParentDraft(title, usableBody);

      const reply1 = buildListeningReply(platform);
      const reply2 = `感想・映画リクエストはこちら👇\n${FORM_URL}\n#リルパル`;

      drafts.push({
        title,
        parent,
        reply1,
        reply2,
        sourceListenUrl: listenUrl,
        spotifyUrl,
        appleUrl: platform.apple_url || null,
        youtubeUrl: platform.youtube_url || null,
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
