const fs = require('fs');
const fetch = require('node-fetch');
const xml2js = require('xml2js');

const RSS_URL = 'https://anchor.fm/s/10422ca68/podcast/rss';
const OUTPUT_DIR = 'data';
const OUTPUT_FILE = 'data/episodes.json';

(async () => {
  // data ディレクトリがなければ作る
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR);
  }

  const res = await fetch(RSS_URL);
  const xml = await res.text();

  const parsed = await xml2js.parseStringPromise(xml);
  const items = parsed.rss.channel[0].item;

  const episodes = items
    .map(item => {
      const id = item['spotify:episodeId']?.[0];
      if (!id) return null;
      return `https://open.spotify.com/episode/${id}`;
    })
    .filter(Boolean);

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(episodes, null, 2));
  console.log(`Saved ${episodes.length} Spotify episode links`);
})();
