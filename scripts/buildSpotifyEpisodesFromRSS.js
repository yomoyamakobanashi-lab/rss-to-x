const fs = require('fs');

const RSS_URL = 'https://anchor.fm/s/10422ca68/podcast/rss';
const OUTPUT_DIR = 'data';
const OUTPUT_FILE = 'data/spotify_episodes.json';

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
    .replace(/[#＃]/g, '')
    .replace(/[【】「」『』"“”'’]/g, '')
    .replace(/\s+/g, ' ')
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

(async () => {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const res = await fetch(RSS_URL, {
    headers: { 'user-agent': 'reelpal-rss-to-x/1.0 (+https://github.com/yomoyamakobanashi-lab/rss-to-x)' }
  });
  if (!res.ok) throw new Error(`Failed to fetch RSS: ${res.status} ${res.statusText}`);

  const xml = await res.text();
  const items = [...xml.matchAll(/<item(?:\s[^>]*)?>([\s\S]*?)<\/item>/gi)].map(m => m[1]);

  const episodes = items.map((item, index) => {
    const title = tag(item, 'title');
    if (!title) return null;

    const spotifyId = tag(item, 'spotify:episodeId');
    let spotifyUrl = spotifyId && /^[A-Za-z0-9]+$/.test(spotifyId)
      ? `https://open.spotify.com/episode/${spotifyId}`
      : findSpotifyEpisodeUrl(item);

    const guid = tag(item, 'guid');
    const link = tag(item, 'link');

    return {
      index,
      title,
      normalizedTitle: normalizeTitle(title),
      spotifyUrl,
      guid,
      link,
      pubDate: tag(item, 'pubDate')
    };
  }).filter(Boolean);

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(episodes, null, 2) + '\n', 'utf8');

  const withSpotify = episodes.filter(ep => ep.spotifyUrl).length;
  console.log(`Saved ${episodes.length} RSS episodes to ${OUTPUT_FILE}`);
  console.log(`RSS episodes with exact Spotify URL: ${withSpotify}`);
})();
