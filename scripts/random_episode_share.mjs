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
 * Compose text with placeholders:
 *  - {title}
 *  - {url}
 * If {url} is missing in phrase, append it.
 */
function renderTemplate(phrase, { title, url }) {
  let p = String(phrase);
  if (!p.includes("{url}")) p = `${p} {url}`;
  return p.replaceAll("{title}", title).replaceAll("{url}", url);
}

/**
 * Fit title into X post length using official weighted counting (twitter-text).
 * URLs count as 23, emoji/CJK count 2, etc.
 */
function fitTitleTo280(phrase, rawTitle, url) {
  const ell = "…";
  const title = String(rawTitle ?? "").trim().replace(/\s+/g, " ");

  // If phrase doesn't use title, validate whole text only once.
  if (!String(phrase).includes("{title}")) {
    const text = renderTemplate(phrase, { title, url });
    const r = parseTweet(text);
    if (!r.valid) return { text: hardTrimWholeText(text), finalTitle: title };
    return { text, finalTitle: title };
  }

  // Try full title
  {
    const text = renderTemplate(phrase, { title, url });
    if (parseTweet(text).valid) return { text, finalTitle: title };
  }

  // Binary search on Unicode codepoints (safe for emoji/surrogates)
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
  if (!parseTweet(text).valid) {
    text = hardTrimWholeText(text);
  }
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
// RSS + Spotify URL resolver (no Spotify env required)
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

// Optional Spotify lookup (only if env exists)
async function spotifyGetTokenOptional() {
  const id = env("SPOTIFY_CLIENT_ID");
  const secret = env("SPOTIFY_CLIENT_SECRET");
  if (!id || !secret) return null;

  const basic = Buffer.from(`${id}:${secret}`).toString("base64");
  const res = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: {
      authorization: `Basic ${basic}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ grant_type: "client_credentials" }),
  });

  if (!res.ok) return null;
  const json = await res.json();
  return json?.access_token ?? null;
}

function normalizeForMatch(s) {
  return String(s ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[’'"]/g, "")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function scoreEpisodeMatch({ rssTitle, rssDateISO, spName, spDate }) {
  const a = normalizeForMatch(rssTitle);
  const b = normalizeForMatch(spName);
  if (!a || !b) return 0;

  let score = 0;
  if (a === b) score += 1000;
  if (b.includes(a)) score += 300;
  if (a.includes(b)) score += 200;

  const at = new Set(a.split(" "));
  const bt = new Set(b.split(" "));
  let overlap = 0;
  for (const x of at) if (bt.has(x)) overlap++;
  score += overlap * 25;

  if (rssDateISO && spDate) {
    const rss = new Date(rssDateISO).getTime();
    const sp = new Date(spDate).getTime();
    if (Number.isFinite(rss) && Number.isFinite(sp)) {
      const days = Math.abs(rss - sp) / (1000 * 60 * 60 * 24);
      score += Math.max(0, 200 - Math.min(200, days * 20));
    }
  }
  return score;
}

async function tryResolveSpotifyEpisodeUrlViaSearch(token, rssTitle, rssDateISO) {
  if (!token) return null;
  const q = `"${String(rssTitle).replace(/"/g, "")}"`;
  const url = `https://api.spotify.com/v1/search?type=episode&limit=10&q=${encodeURIComponent(q)}`;
  const res = await fetch(url, { headers: { authorization: `Bearer ${token}` } });
  if (!res.ok) return null;

  const json = await res.json();
  const items = json?.episodes?.items ?? [];
  if (!items.length) return null;

  let best = null;
  let bestScore = -1;
  for (const ep of items) {
    const s = scoreEpisodeMatch({
      rssTitle,
      rssDateISO,
      spName: ep?.name,
      spDate: ep?.release_date,
    });
    if (s > bestScore) {
      bestScore = s;
      best = ep;
    }
  }
  if (best && bestScore >= 250 && best?.external_urls?.spotify) return best.external_urls.spotify;
  return null;
}

// -------------------------
// main
// -------------------------
async function main() {
  const xClient = new TwitterApi({
    appKey: mustEnv("X_API_KEY"),
    appSecret: mustEnv("X_API_KEY_SECRET"),
    accessToken: mustEnv("X_ACCESS_TOKEN"),
    accessSecret: mustEnv("X_ACCESS_TOKEN_SECRET"),
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
  const rssDateISO = picked.isoDate || picked.pubDate || null;

  // Resolve URL preference:
  // 1) Try Spotify episode direct URL by scraping episode page
  // 2) Optional Spotify API search (if env exists)
  // 3) Fallback to RSS link
  let episodeUrl = await tryExtractSpotifyEpisodeUrlFromPage(episodePage);

  if (!episodeUrl) {
    const token = await spotifyGetTokenOptional();
    if (token) {
      episodeUrl = await tryResolveSpotifyEpisodeUrlViaSearch(token, episodeTitle, rssDateISO);
    }
  }

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
