import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import Parser from "rss-parser";
import twitterText from "twitter-text";
import { TwitterApi } from "twitter-api-v2";

const { parseTweet } = twitterText;

const DEFAULT_RSS = "https://anchor.fm/s/10422ca68/podcast/rss";
const HOOKS_PATH = path.join(process.cwd(), "data", "weekly_hooks.txt");

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

function readLinesIfExists(p) {
  if (!fs.existsSync(p)) return null;
  const raw = fs.readFileSync(p, "utf8");
  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  return lines.length ? lines : null;
}

function renderTemplate(phrase, { title, url }) {
  let p = String(phrase);
  if (!p.includes("{url}")) p = `${p}\n{url}`;
  return p.replaceAll("{title}", title).replaceAll("{url}", url);
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

  // フルタイトルを試す
  {
    const text = renderTemplate(phrase, { title, url });
    if (parseTweet(text).valid) return { text, finalTitle: title };
  }

  // codepoints 単位の二分探索（絵文字・CJKでも安全）
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

// -------------------------
// RSS + Spotify URL resolver
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

function toDateMs(item) {
  const d = item?.isoDate || item?.pubDate;
  const ms = d ? new Date(d).getTime() : NaN;
  return Number.isFinite(ms) ? ms : 0;
}

function distinctByKey(items, keyFn) {
  const seen = new Set();
  const out = [];
  for (const it of items) {
    const k = keyFn(it);
    if (!k) continue;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(it);
  }
  return out;
}

function selectThreeEpisodes(items) {
  // まず日付降順
  const sorted = [...items].sort((a, b) => toDateMs(b) - toDateMs(a));

  // 重複っぽいのを排除（link/guid）
  const uniq = distinctByKey(sorted, (it) => it.link || it.guid || it.title);

  if (uniq.length <= 3) return uniq.slice(0, 3);

  // 選び方（安定運用向け）：
  // ① 最新
  // ② 直近90日以内からランダム（最新以外）
  // ③ それより古い回からランダム
  const newest = uniq[0];

  const now = Date.now();
  const days90 = 90 * 24 * 60 * 60 * 1000;

  const recentPool = uniq.slice(1).filter((it) => toDateMs(it) >= now - days90);
  const oldPool = uniq.slice(1).filter((it) => toDateMs(it) < now - days90);

  const picked = [newest];
  const used = new Set([newest.link || newest.guid || newest.title]);

  function pickFrom(pool) {
    const candidates = pool.filter((it) => {
      const k = it.link || it.guid || it.title;
      return k && !used.has(k);
    });
    if (!candidates.length) return null;
    const it = pickRandom(candidates);
    used.add(it.link || it.guid || it.title);
    return it;
  }

  const second = pickFrom(recentPool) || pickFrom(uniq.slice(1));
  if (second) picked.push(second);

  const third = pickFrom(oldPool) || pickFrom(uniq.slice(1));
  if (third) picked.push(third);

  // それでも足りなければ適当に補完
  while (picked.length < 3) {
    const it = pickFrom(uniq.slice(1));
    if (!it) break;
    picked.push(it);
  }

  return picked.slice(0, 3);
}

// -------------------------
// main
// -------------------------
async function main() {
  const xClient = new TwitterApi({
    appKey: mustEnv("X_API_KEY"),
    appSecret: mustEnv("X_API_SECRET"),
    accessToken: mustEnv("X_ACCESS_TOKEN"),
    accessSecret: mustEnv("X_ACCESS_SECRET"),
  });

  const hooks =
    readLinesIfExists(HOOKS_PATH) ?? [
      "週末の過去回セレクト、3本置いていきます。",
      "今週の回遊用に、過去回を3本まとめます。",
      "タイムライン補給：過去回3本、貼ります。",
      "聴き逃し救済。今週の3本です。",
      "過去回ガチャ、今週の3本いきます。",
    ];

  // RSS
  const parser = new Parser();
  const rssUrl = env("RSS_URL", DEFAULT_RSS);

  let feed;
  try {
    feed = await parser.parseURL(rssUrl);
  } catch {
    const xml = await fetchText(rssUrl);
    feed = await parser.parseString(xml);
  }

  const allItems = (feed?.items ?? []).filter((it) => it?.title);
  if (!allItems.length) throw new Error("No RSS items found.");

  const episodes = selectThreeEpisodes(allItems);

  // 1投目（導入）
  const hook = pickRandom(hooks);
  const firstTextRaw = `${hook}\n（①〜③で貼ります）\n#ReelPal`;
  const firstText = parseTweet(firstTextRaw).valid ? firstTextRaw : hardTrimWholeText(firstTextRaw);

  console.log("[thread] tweet1:", firstText);
  const root = await xClient.v2.tweet(firstText);
  let replyToId = root?.data?.id;

  if (!replyToId) throw new Error("Failed to post first tweet (no tweet id).");

  // 2〜4投目（①②③）
  for (let i = 0; i < episodes.length; i++) {
    const it = episodes[i];
    const title = String(it.title).trim();
    const pageUrl = it.link || it.guid || rssUrl;

    let url = await tryExtractSpotifyEpisodeUrlFromPage(pageUrl);
    if (!url) url = pageUrl;

    // 各投目のテンプレ（タイトルが長い場合は自動省略）
    const phrase = `${["①", "②", "③"][i] ?? "・"} {title}\n{url}`;
    const { text } = fitTitleTo280(phrase, title, url);

    const r = parseTweet(text);
    console.log(`[thread] tweet${i + 2} weightedLength:`, r.weightedLength, "valid:", r.valid);
    console.log(`[thread] tweet${i + 2}:`, text);

    const res = await xClient.v2.tweet(text, {
      reply: { in_reply_to_tweet_id: replyToId },
    });

    const newId = res?.data?.id;
    if (!newId) throw new Error(`Failed to post tweet ${i + 2} (no tweet id).`);
    replyToId = newId;
  }

  console.log("OK: weekly digest thread posted.");
}

main().catch((e) => {
  console.error(e?.stack || e);
  process.exit(1);
});
