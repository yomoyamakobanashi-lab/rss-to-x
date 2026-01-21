import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import Parser from "rss-parser";
import twitterText from "twitter-text";
import { TwitterApi } from "twitter-api-v2";

const { parseTweet } = twitterText;

const DEFAULT_RSS = "https://anchor.fm/s/10422ca68/podcast/rss";
const PHRASES_PATH = path.join(process.cwd(), "data", "phrases.txt");

// -------------------------
// env helpers
// -------------------------
function env(name, fallback = undefined) {
  const v = process.env[name];
  return (v && String(v).trim()) ? String(v).trim() : fallback;
}
function mustEnv(name) {
  const v = env(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

// -------------------------
// utils
// -------------------------
function pickRandom(arr) {
  return arr[crypto.randomInt(0, arr.length)];
}

function readPhrasesFile() {
  if (!fs.existsSync(PHRASES_PATH)) return null;
  const raw = fs.readFileSync(PHRASES_PATH, "utf8");
  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  return lines.length ? lines : null;
}

/**
 * placeholders:
 *  - {title}
 *  - {url}
 * If {url} missing, append.
 */
function renderTemplate(phrase, { title, url }) {
  let p = String(phrase);
  if (!p.includes("{url}")) p = `${p} {url}`;
  return p.replaceAll("{title}", title).replaceAll("{url}", url);
}

/**
 * Xのweighted文字数で収まるように「タイトルだけ」省略（…付）。
 * URLは 23 として扱われ、CJK/絵文字も正しく加重される（twitter-text）。
 */
function fitTitleTo280(phrase, rawTitle, url) {
  const ell = "…";
  const title = String(rawTitle ?? "").trim().replace(/\s+/g, " ");

  // phraseがtitleを使わないなら、全体だけ検証
  if (!String(phrase).includes("{title}")) {
    const text = renderTemplate(phrase, { title, url });
    const r = parseTweet(text);
    if (!r.valid) return { text: hardTrimWholeText(text), finalTitle: title };
    return { text, finalTitle: title };
  }

  // まずフルタイトル
  {
    const text = renderTemplate(phrase, { title, url });
    if (parseTweet(text).valid) return { text, finalTitle: title };
  }

  // Unicode codepoints 単位で二分探索（絵文字でも壊れない）
  const cps = [...title];
  let lo = 0;
  let hi = cps.length;
  let best = "";

  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    const cand = cps.slice(0, mid).join("") + (mid < cps.length ? ell : "");
    const text = renderTemplate(phrase, { title: cand, url });
    if (parseTweet(text).valid) {
      best = cand;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  const finalTitle = best || "";
  let text = renderTemplate(phrase, { title: finalTitle, url });
  if (!parseTweet(text).valid) text = hardTrimWholeText(text);
  return { text, finalTitle };
}

function hardTrimWholeText(text) {
  const ell = "…";
  const cps = [...String(text)];
  let lo = 0;
  let hi = cps.length;
  let best = "";

  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    const cand = cps.slice(0, mid).join("") + (mid < cps.length ? ell : "");
    if (parseTweet(cand).valid) {
      best = cand;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return best || String(text).slice(0, 10);
}

// -------------------------
// RSS -> URL resolver
// -------------------------
async function fetchText(url) {
  const res = await fetch(url, {
    redirect: "follow",
    headers: {
      "user-agent": "rss-to-x/1.0 (+github actions)",
      "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
  });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText} (${url})`);
  return await res.text();
}

async function tryExtractSpotifyEpisodeUrlFromPage(pageUrl) {
  if (!pageUrl) return null;
  try {
    const html = await fetchText(pageUrl);
    const m = html.match(/https?:\/\/open\.spotify\.com\/episode\/[A-Za-z0-9]+(?:\?[^\s"'<>]*)?/);
    return m ? m[0] : null;
  } catch {
    return null;
  }
}

// -------------------------
// main
// -------------------------
async function main() {
  // あなたの Secrets 名に合わせてここを固定
  const xClient = new TwitterApi({
    appKey: mustEnv("X_API_KEY"),
    appSecret: mustEnv("X_API_SECRET"),
    accessToken: mustEnv("X_ACCESS_TOKEN"),
    accessSecret: mustEnv("X_ACCESS_SECRET"),
  });

  const phrases = readPhrasesFile() ?? [
    "過去回ランダム：{title} {url}",
    "今日の1本：{title} {url}",
    "聴き逃し防止：{title} {url}",
  ];

  const parser = new Parser();
  const rssUrl = env("RSS_URL", DEFAULT_RSS);

  let feed;
  try {
    feed = await parser.parseURL(rssUrl);
  } catch {
    const xml = await fetchText(rssUrl);
    feed = await parser.parseString(xml);
  }

  const items = (feed?.items ?? []).filter((it) => it?.title);
  if (!items.length) throw new Error("No RSS items found.");

  const picked = pickRandom(items);
  const episodeTitle = String(picked.title).trim();
  const episodePage = picked.link || picked.guid || null;

  // まずエピソードページから Spotify episode URL を拾う（取れない場合はフォールバック）
  let episodeUrl = await tryExtractSpotifyEpisodeUrlFromPage(episodePage);
  if (!episodeUrl) {
    episodeUrl = episodePage || rssUrl;
    console.warn("[warn] Spotify direct URL not resolved; fallback:", episodeUrl);
  }

  const phrase = pickRandom(phrases);
  const { text } = fitTitleTo280(phrase, episodeTitle, episodeUrl);

  const r = parseTweet(text);
  console.log("[tweet] weightedLength:", r.weightedLength, "valid:", r.valid);
  console.log("[tweet] text:", text);

  await xClient.v2.tweet(text);
  console.log("OK: posted.");
}

main().catch((e) => {
  console.error(e?.stack || e);
  process.exit(1);
});
