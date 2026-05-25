const fs = require('fs');
const fetch = require('node-fetch');
const xml2js = require('xml2js');

const RSS_URL = 'https://anchor.fm/s/10422ca68/podcast/rss';
const OUTPUT_DIR = 'data';
const OUTPUT_FILE = 'data/spotify_episodes.json';

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

  const match = raw.match(/https:\/\/open\.spotify\.com\/episode\/[A-Za-z0-9]+/);
  return match ? match[0] : null;
}

(async () => {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  const res = await fetch(RSS_URL);
  if (!res.ok) {
    throw new Error(`Failed to fetch RSS: ${res.status} ${res.statusText}`);
  }

  const xml = await res.text();
  const parsed = await xml2js.parseStringPromise(xml);
  const items = parsed?.rss?.channel?.[0]?.item || [];

  const episodes = items
    .map((item, index) => {
      const title = item.title?.[0] || '';
      if (!title) return null;

      let spotifyUrl = null;

      const spotifyId = item['spotify:episodeId']?.[0];
      if (spotifyId) {
        spotifyUrl = `https://open.spotify.com/episode/${spotifyId}`;
      }

      if (!spotifyUrl) {
        spotifyUrl = findSpotifyEpisodeUrl(JSON.stringify(item));
      }

      return {
        index,
        title,
        normalizedTitle: normalizeTitle(title),
        spotifyUrl,
        pubDate: item.pubDate?.[0] || ''
      };
    })
    .filter(Boolean);

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(episodes, null, 2), 'utf8');

  const withSpotify = episodes.filter(ep => ep.spotifyUrl).length;
  console.log(`Saved ${episodes.length} RSS episodes to ${OUTPUT_FILE}`);
  console.log(`RSS episodes with Spotify URL: ${withSpotify}`);
})();
