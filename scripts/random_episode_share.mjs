// .github/scripts/random_episode_share.mjs
import crypto from "node:crypto";

const RSS_URL = process.env.RSS_URL;
const EXCLUDE_DAYS = Number(process.env.EXCLUDE_DAYS ?? "7");

const X_API_KEY = process.env.X_API_KEY;
const X_API_SECRET = process.env.X_API_SECRET;
const X_ACCESS_TOKEN = process.env.X_ACCESS_TOKEN;
const X_ACCESS_SECRET = process.env.X_ACCESS_SECRET;

const SPOTIFY_CLIENT_ID = process.env.SPOTIFY_CLIENT_ID;
const SPOTIFY_CLIENT_SECRET = process.env.SPOTIFY_CLIENT_SECRET;

function mustEnv(name, value) {
  if (!value) throw new Error(`Missing env: ${name}`);
  return value;
}

function rfc3986Encode(str) {
  return encodeURIComponent(str)
    .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

function decodeXmlEntities(s = "") {
  return s
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

function pickRandom(arr) {
  return arr[crypto.randomInt(0, arr.length)];
}

function parseRss(xml) {
  const channelBlock = (xml.match(/<channel\b[^>]*>[\s\S]*?<\/channel>/i) || [xml])[0];
  const channelTitle = decodeXmlEntities(
    (channelBlock.match(/<title>([\s\S]*?)<\/title>/i) || [,""])[1]
  ).trim();

  const items = [...xml.matchAll(/<item\b[^>]*>[\s\S]*?<\/item>/gi)].map((m) => m[0]);

  const parsed = items.map((raw) => {
    const title = decodeXmlEntities((raw.match(/<title>([\s\S]*?)<\/title>/i) || [,""])[1]).trim();
    const link  = decodeXmlEntities((raw.match(/<link>([\s\S]*?)<\/link>/i) || [,""])[1]).trim();
    const pub   = decodeXmlEntities((raw.match(/<pubDate>([\s\S]*?)<\/pubDate>/i) || [,""])[1]).trim();
    const pubDate = pub ? new Date(pub) : null;

    return { title, link, pubDate, raw };
  }).filter(x => x.title);

  return { channelTitle, items: parsed };
}

function extractSpotifyUrlFromText(text) {
  // 1) open.spotify.com/episode/ID
  const m1 = text.match(/https?:\/\/open\.spotify\.com\/episode\/([A-Za-z0-9]+)/i);
  if (m1) return `https://open.spotify.com/episode/${m1[1]}`;

  // 2) spotify:episode:ID
  const m2 = text.match(/spotify:episode:([A-Za-z0-9]+)/i);
  if (m2) return `https://open.spotify.com/episode/${m2[1]}`;

  // 3) spotify.link short (変換は保証できないのでそのまま)
  const m3 = text.match(/https?:\/\/spotify\.link\/[A-Za-z0-9]+/i);
  if (m3) return m3[0];

  return null;
}

async function spotifyGetToken() {
  mustEnv("SPOTIFY_CLIENT_ID", SPOTIFY_CLIENT_ID);
  mustEnv("SPOTIFY_CLIENT_SECRET", SPOTIFY_CLIENT_SECRET);

  const basic = Buffer.from(`${SPOTIFY_CLIENT_ID}:${SPOTIFY_CLIENT_SECRET}`).toString("base64");
  const res = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: "grant_type=client_credentials",
  });

  if (!res.ok) {
    const t = await res.text();
    throw new Error(`Spotify token failed: ${res.status} ${t}`);
  }
  const j = await res.json();
  return j.access_token;
}

async function spotifySearchShowId(token, channelTitle) {
  const q = encodeURIComponent(channelTitle);
  const url = `https://api.spotify.com/v1/search?q=${q}&type=show&limit=5`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`Spotify show search failed: ${res.status} ${await res.text()}`);
  const j = await res.json();
  const items = j?.shows?.items ?? [];
  if (!items.length) return null;

  // シンプルに「完全一致優先 → 部分一致 → 先頭」
  const ct = channelTitle.toLowerCase();
  const exact = items.find(s => (s.name ?? "").toLowerCase() === ct);
  if (exact) return exact.id;

  const partial = items.find(s => (s.name ?? "").toLowerCase().includes(ct) || ct.includes((s.name ?? "").toLowerCase()));
  return (partial ?? items[0]).id;
}

function normalizeTitle(s) {
  return (s ?? "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[【】［］\[\]（）\(\)「」『』"“”'’]/g, "")
    .trim();
}

async function spotifyResolveEpisodeUrl(token, showId, episodeTitle, pubDate) {
  const target = normalizeTitle(episodeTitle);
  const targetTime = pubDate instanceof Date && !Number.isNaN(pubDate.getTime()) ? pubDate.getTime() : null;

  let best = null; // { url, score }
  const limit = 50;

  for (let offset = 0; offset <= 500; offset += limit) {
    const url = `https://api.spotify.com/v1/shows/${showId}/episodes?market=JP&limit=${limit}&offset=${offset}`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`Spotify show episodes failed: ${res.status} ${await res.text()}`);

    const j = await res.json();
    const eps = j?.items ?? [];

    for (const ep of eps) {
      const name = ep?.name ?? "";
      const nrm = normalizeTitle(name);

      // タイトル一致の強さ（完全一致優先）
      let titleScore = 0;
      if (nrm === target) titleScore = 100;
      else if (nrm.includes(target) || target.includes(nrm)) titleScore = 70;
      else continue;

      // 日付差（pubDateが取れるときだけ加点/減点）
      let dateScore = 0;
      if (targetTime && ep?.release_date) {
        const epTime = new Date(ep.release_date).getTime();
        const diffDays = Math.abs(epTime - targetTime) / (1000 * 60 * 60 * 24);
        dateScore = Math.max(0, 30 - diffDays); // 最大30点
      }

      const score = titleScore + dateScore;
      if (!best || score > best.score) {
        best = { url: ep?.external_urls?.spotify ?? null, score };
      }
    }

    if (!j?.next) break;
  }

  return best?.url ?? null;
}

const TEMPLATES = [
  "{title}\n{url}",
  "今日の1本：{title}\n{url}",
  "過去回ランダム投下。{title}\n{url}",
  "通勤・通学のお供に：{title}\n{url}",
  "作業BGMにちょうどいい回：{title}\n{url}",
  "気分転換にどうぞ：{title}\n{url}",
  "この回、地味に刺さる。{title}\n{url}",
  "再生ボタンを押す理由がここにある：{title}\n{url}",
  "週の真ん中にこの回を。{title}\n{url}",
  "夜に聴くと味が変わる回：{title}\n{url}",
  "朝のテンション調整回：{title}\n{url}",
  "昼休みの逃避行：{title}\n{url}",
  "帰宅路線に最適解：{title}\n{url}",
  "聞き逃し救済：{title}\n{url}",
  "“今”じゃなくて“あの頃”の回：{title}\n{url}",
  "過去回ガチャ結果はこちら：{title}\n{url}",
  "あなたの耳に、ランダムで。{title}\n{url}",
  "1エピソード、1リフレッシュ：{title}\n{url}",
  "今日はこれでいこう：{title}\n{url}",
  "気になったら即再生：{title}\n{url}",
  "語りが乗ってる回を引いた。{title}\n{url}",
  "会話の温度がちょうどいい回：{title}\n{url}",
  "眠気覚ましに投げとく：{title}\n{url}",
  "深掘り欲が満たされる回：{title}\n{url}",
  "やたら濃い回、当選：{title}\n{url}",
  "この回から入るの、アリ。{title}\n{url}",
  "初見でもいける回：{title}\n{url}",
  "“ながら聴き”推奨：{title}\n{url}",
  "テンポ重視派へ：{title}\n{url}",
  "雑談のキレがある回：{title}\n{url}",
  "話題の散らかり具合が最高：{title}\n{url}",
  "好きな人は絶対好きな回：{title}\n{url}",
  "映画の話、してます：{title}\n{url}",
  "一旦これ聴いて落ち着こう：{title}\n{url}",
  "“時間が溶ける”やつ：{title}\n{url}",
  "たぶん今日のあなたに必要：{title}\n{url}",
  "おすすめというより、指名手配：{title}\n{url}",
  "これは嗜好品。{title}\n{url}",
  "脳内シアター開演：{title}\n{url} #リルパル",
  "過去回を一緒に掘ろう：{title}\n{url} #リルパル",
  "ReelFriendsInTokyo 過去回ガチャ：{title}\n{url} #ReelPal",
  "今夜の相棒：{title}\n{url} #ReelPal",
  "この回の空気感、良い。{title}\n{url} #リルパル",
  "タイトルだけで勝ってる回：{title}\n{url}",
  "耳が暇ならこれ：{title}\n{url}",
  "おすすめの仕方が雑でごめん。聴けばわかる：{title}\n{url}",
  "“とりあえず再生”でOK：{title}\n{url}",
  "タイムラインに過去回を置いておく：{title}\n{url}",
  "一周回ってこの回：{title}\n{url}"
];

function buildTweet(template, title, url) {
  let t = title;
  for (let i = 0; i < 200; i++) {
    const text = template.replace("{title}", t).replace("{url}", url).trim();
    if (text.length <= 280) return text;
    t = t.slice(0, Math.max(0, t.length - 1));
    if (!t) break;
  }
  return `${title.slice(0, 60)}\n${url}`.trim();
}

function oauth1Header({ method, url, consumerKey, consumerSecret, token, tokenSecret }) {
  const oauthParams = {
    oauth_consumer_key: consumerKey,
    oauth_nonce: crypto.randomBytes(16).toString("hex"),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_token: token,
    oauth_version: "1.0",
  };

  const baseParams = Object.entries(oauthParams)
    .sort(([a],[b]) => a.localeCompare(b))
    .map(([k,v]) => `${rfc3986Encode(k)}=${rfc3986Encode(v)}`)
    .join("&");

  const baseString = [
    method.toUpperCase(),
    rfc3986Encode(url),
    rfc3986Encode(baseParams),
  ].join("&");

  const signingKey = `${rfc3986Encode(consumerSecret)}&${rfc3986Encode(tokenSecret)}`;
  const signature = crypto.createHmac("sha1", signingKey).update(baseString).digest("base64");

  const headerParams = { ...oauthParams, oauth_signature: signature };
  const header = "OAuth " + Object.entries(headerParams)
    .sort(([a],[b]) => a.localeCompare(b))
    .map(([k,v]) => `${rfc3986Encode(k)}="${rfc3986Encode(v)}"`)
    .join(", ");

  return header;
}

async function postToX(text) {
  mustEnv("X_API_KEY", X_API_KEY);
  mustEnv("X_API_SECRET", X_API_SECRET);
  mustEnv("X_ACCESS_TOKEN", X_ACCESS_TOKEN);
  mustEnv("X_ACCESS_SECRET", X_ACCESS_SECRET);

  const url = "https://api.x.com/2/tweets";
  const auth = oauth1Header({
    method: "POST",
    url,
    consumerKey: X_API_KEY,
    consumerSecret: X_API_SECRET,
    token: X_ACCESS_TOKEN,
    tokenSecret: X_ACCESS_SECRET,
  });

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: auth,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  const bodyText = await res.text();
  if (!res.ok) throw new Error(`X post failed: ${res.status} ${bodyText}`);
  return bodyText;
}

async function main() {
  mustEnv("RSS_URL", RSS_URL);

  const rssRes = await fetch(RSS_URL);
  if (!rssRes.ok) throw new Error(`RSS fetch failed: ${rssRes.status} ${await rssRes.text()}`);
  const xml = await rssRes.text();

  const { channelTitle, items } = parseRss(xml);
  if (!items.length) throw new Error("No RSS items found.");

  // “過去回”定義：直近EXCLUDE_DAYS日を除外。取れない場合は最新1件だけ除外。
  const now = Date.now();
  const past = items.filter(it => it.pubDate instanceof Date && !Number.isNaN(it.pubDate.getTime()))
    .filter(it => (now - it.pubDate.getTime()) >= EXCLUDE_DAYS * 24 * 60 * 60 * 1000);

  const pool = past.length ? past : items.slice(1); // fallback
  if (!pool.length) throw new Error("No eligible episodes after filtering.");

  const picked = pickRandom(pool);

  // まずRSS内からspotify URLを探す
  let spotifyUrl =
    extractSpotifyUrlFromText(picked.raw) ||
    (picked.link ? extractSpotifyUrlFromText(picked.link) : null);

  // 無ければSpotify APIで照合して確定
  if (!spotifyUrl) {
    const token = await spotifyGetToken();
    const showId = await spotifySearchShowId(token, channelTitle || "Reel Friends in Tokyo");
    if (!showId) {
      throw new Error("Spotify showId not found. (Check channel title or set show link in RSS.)");
    }
    spotifyUrl = await spotifyResolveEpisodeUrl(token, showId, picked.title, picked.pubDate);
    if (!spotifyUrl) {
      throw new Error(`Spotify episode URL not resolved for title: ${picked.title}`);
    }
  }

  const template = pickRandom(TEMPLATES);
  const tweet = buildTweet(template, picked.title, spotifyUrl);

  console.log("Picked:", picked.title);
  console.log("Spotify:", spotifyUrl);
  console.log("Tweet:\n" + tweet);

  const result = await postToX(tweet);
  console.log("X result:", result);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
