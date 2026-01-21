import fs from 'fs';
import fetch from 'node-fetch';
import { parseStringPromise } from 'xml2js';

const RSS_URL = 'https://anchor.fm/s/10422ca68/podcast/rss';

const res = await fetch(RSS_URL);
const xml = await res.text();
const json = await parseStringPromise(xml);

const items = json.rss.channel[0].item;

const episodes = items
  .map(item => {
    const id = item['spotify:episodeId']?.[0];
    if (!id) return null;
    return `https://open.spotify.com/episode/${id}`;
  })
  .filter(Boolean);

fs.writeFileSync('data/episodes.json', JSON.stringify(episodes, null, 2));
console.log(`Saved ${episodes.length} episodes`);
