const fs = require('fs');

const RSS_URL = 'https://anchor.fm/s/10422ca68/podcast/rss';
const SPOTIFY_SHOW_ID = '4o8l9DJWMuwUht2pvkEytS';
const OUTPUT_DIR = 'data';
const OUTPUT_FILE = 'data/spotify_episodes.json';
const CANONICAL_FILES = [
  'data/funny_clip_posts_all_episodes.json',
  'data/funny_clip_legacy_canonical.json'
];

function decodeXml(value) {
  return String(value || '')
    .replace(/^<!\[CDATA\[/, '')
    .replace(/\]\]>$/, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}

function tag(block, name) {
  const escaped = name.replace(':', '\\:');
  const match = block.match(new RegExp(`<${escaped}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${escaped}>`, 'i'));
  return match ? decodeXml(match[1]) : '';
}

function normalizeTitle(title) {
  return String(title || '')
    .normalize('NFKC')
    .replace(/[\u200b\ufeff\ufffc]/g, '')
    .replace(/[\s　]+/g, '')
    .replace(/[『』「」〖〗【】#\-_—–:：・,.!?！？…（）()\[\]“”"'’〜~]/g, '')
    .trim()
    .toLowerCase();
}

function findSpotifyEpisodeUrl(text) {
  const raw = String(text || '')
    .replace(/\\u002F/g, '/')
    .replace(/\\\//g, '/')
    .replace(/&amp;/g, '&');
  const match = raw.match(/https:\/\/open\.spotify\.com\/(?:embed\/)?episode\/([A-Za-z0-9]+)/i);
  return match ? `https://open.spotify.com/episode/${match[1]}` : null;
}

async function fetchRssEpisodes() {
  const res = await fetch(RSS_URL, {
    headers: { 'user-agent': 'reelpal-rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)' }
  });
  if (!res.ok) throw new Error(`Failed to fetch RSS: ${res.status} ${res.statusText}`);

  const xml = await res.text();
  const items = [...xml.matchAll(/<item(?:\s[^>]*)?>([\s\S]*?)<\/item>/gi)].map(m => m[1]);
  return items.map((item, index) => {
    const title = tag(item, 'title');
    if (!title) return null;
    const spotifyId = tag(item, 'spotify:episodeId');
    const spotifyUrl = spotifyId && /^[A-Za-z0-9]+$/.test(spotifyId)
      ? `https://open.spotify.com/episode/${spotifyId}`
      : findSpotifyEpisodeUrl(item);
    return {
      index,
      title,
      normalizedTitle: normalizeTitle(title),
      spotifyUrl,
      guid: tag(item, 'guid'),
      link: tag(item, 'link'),
      pubDate: tag(item, 'pubDate')
    };
  }).filter(Boolean);
}

async function spotifyAccessToken() {
  const clientId = String(process.env.SPOTIFY_CLIENT_ID || '').trim();
  const clientSecret = String(process.env.SPOTIFY_CLIENT_SECRET || '').trim();
  if (!clientId || !clientSecret) return null;

  const body = new URLSearchParams({ grant_type: 'client_credentials' });
  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: {
      authorization: `Basic ${Buffer.from(`${clientId}:${clientSecret}`).toString('base64')}`,
      'content-type': 'application/x-www-form-urlencoded'
    },
    body
  });
  if (!res.ok) throw new Error(`Spotify token request failed: ${res.status} ${await res.text()}`);
  const data = await res.json();
  if (!data.access_token) throw new Error('Spotify token response did not contain access_token');
  return data.access_token;
}

async function fetchSpotifyCatalog(token) {
  const episodes = [];
  for (let offset = 0; ; offset += 50) {
    const url = `https://api.spotify.com/v1/shows/${SPOTIFY_SHOW_ID}/episodes?market=JP&limit=50&offset=${offset}`;
    const res = await fetch(url, { headers: { authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`Spotify show episodes request failed: ${res.status} ${await res.text()}`);
    const data = await res.json();
    for (const item of data.items || []) {
      if (!item?.name || !item?.id) continue;
      episodes.push({
        title: item.name,
        normalizedTitle: normalizeTitle(item.name),
        spotifyUrl: item.external_urls?.spotify || `https://open.spotify.com/episode/${item.id}`,
        spotifyId: item.id,
        releaseDate: item.release_date || ''
      });
    }
    if (!data.next) break;
  }
  return episodes;
}

function mergeExactSpotifyUrls(rssEpisodes, spotifyEpisodes) {
  const byTitle = new Map();
  for (const ep of spotifyEpisodes) {
    if (!ep.normalizedTitle) continue;
    byTitle.set(ep.normalizedTitle, ep);
  }

  return rssEpisodes.map(ep => {
    const exact = byTitle.get(ep.normalizedTitle);
    return exact
      ? { ...ep, spotifyUrl: exact.spotifyUrl, spotifyId: exact.spotifyId, releaseDate: exact.releaseDate }
      : ep;
  });
}

function loadVerifiedSeeds() {
  const byTitle = new Map();
  const sources = [OUTPUT_FILE, ...CANONICAL_FILES];
  for (const file of sources) {
    if (!fs.existsSync(file)) continue;
    let rows;
    try {
      rows = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch (error) {
      console.warn(`[WARN] Could not read Spotify seed file ${file}: ${error.message}`);
      continue;
    }
    if (!Array.isArray(rows)) continue;
    for (const row of rows) {
      const title = normalizeTitle(row.title || row.episode_title);
      const url = String(row.spotifyUrl || row.spotify_url || '').trim();
      if (!title || !url.startsWith('https://open.spotify.com/episode/')) continue;
      const existing = byTitle.get(title);
      if (existing && existing !== url) {
        throw new Error(`Conflicting verified Spotify URLs for: ${title}`);
      }
      byTitle.set(title, url);
    }
  }
  return byTitle;
}

function preserveVerifiedSpotifyUrls(rssEpisodes, seeds) {
  return rssEpisodes.map(ep => {
    if (ep.spotifyUrl?.startsWith('https://open.spotify.com/episode/')) return ep;
    let preserved = seeds.get(ep.normalizedTitle);
    if (!preserved) {
      const matches = new Set();
      for (const [knownTitle, url] of seeds.entries()) {
        if (Math.min(knownTitle.length, ep.normalizedTitle.length) < 12) continue;
        if (knownTitle.includes(ep.normalizedTitle) || ep.normalizedTitle.includes(knownTitle)) {
          matches.add(url);
        }
      }
      if (matches.size === 1) preserved = [...matches][0];
    }
    return preserved ? { ...ep, spotifyUrl: preserved } : ep;
  });
}

(async () => {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const verifiedSeeds = loadVerifiedSeeds();
  const rssEpisodes = await fetchRssEpisodes();
  let episodes = preserveVerifiedSpotifyUrls(rssEpisodes, verifiedSeeds);
  const token = await spotifyAccessToken();

  if (token) {
    const spotifyEpisodes = await fetchSpotifyCatalog(token);
    episodes = mergeExactSpotifyUrls(episodes, spotifyEpisodes);
    console.log(`Spotify official catalog episodes fetched: ${spotifyEpisodes.length}`);
  } else {
    console.log(`[WARN] Spotify credentials are not configured; preserved ${verifiedSeeds.size} checked-in verified URL(s).`);
  }

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(episodes, null, 2) + '\n', 'utf8');
  const withSpotify = episodes.filter(ep => ep.spotifyUrl?.startsWith('https://open.spotify.com/episode/')).length;
  console.log(`Saved ${episodes.length} episodes to ${OUTPUT_FILE}`);
  console.log(`Episodes with exact Spotify URL: ${withSpotify}/${episodes.length}`);
})();
