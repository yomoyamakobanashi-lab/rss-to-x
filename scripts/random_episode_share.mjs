import { XMLParser } from "fast-xml-parser";

const RSS_URL = process.env.RSS_URL?.trim();
const X_BEARER_TOKEN = process.env.X_BEARER_TOKEN?.trim();
const SKIP_LATEST = (process.env.SKIP_LATEST ?? "1").trim() === "1";
const DRY_RUN = (process.env.DRY_RUN ?? "0").trim() === "1";

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

function asArray(v) {
  if (!v) return [];
  return Array.isArray(v) ? v : [v];
}

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * RSS item から Spotify エピソード直リンクを抽出
 * - open.spotify.com/episode/{id} がどこかに入っていればそれを採用
 * - 見つからなければ item.link を fallback
 */
function extractSpotifyEpisodeUrl(item) {
  const candidates = [];

  // よくあるフィールド群（RSS/Atomのバリエーション対策）
  if (typeof item?.link === "string") candidates.push(item.link);
  if (typeof item?.guid === "string") candidates.push(item.guid);
  if (typeof item?.id === "string") candidates.push(item.id);

  // enclosure URL
  if (item?.enclosure?.url) candidates.push(item.enclosure.url);
  if (item?.enclosure?.["@_url"]) candidates.push(item.enclosure["@_url"]); // parser設定による

  // itunes系などで入っているケースもあるので、全キーを走査して文字列を拾う
  for (const [k, v] of Object.entries(item ?? {})) {
    if (typeof v === "string") candidates.push(v);
    if (v && typeof v === "object") {
      for (const vv of Object.values(v)) {
        if (typeof vv === "string") candidates.push(vv);
      }
    }
  }

  const joined = candidates.filter(Boolean).join("\n");

  // open.spotify.com/episode/{id}
  const m1 = joined.match(/https?:\/\/open\.spotify\.com\/episode\/[A-Za-z0-9]+/);
  if (m1) return m1[0];

  // open.spotify.com/episode/{id} だが scheme が無いパターン対策
  const m1b = joined.match(/open\.spotify\.com\/episode\/[A-Za-z0-9]+/);
  if (m1b) return `https://${m1b[0]}`;

  // spotify:episode:{id} を拾えた場合
  const m2 = joined.match(/spotify:episode:([A-Za-z0-9]+)/);
  if (m2?.[1]) return `https://open.spotify.com/episode/${m2[1]}`;

  // 最後の保険：link
  return typeof item?.link === "string" ? item.link : "";
}

function buildTweetText({ title, url }) {
  // 文言は複数パターンからランダム
  const templates = [
    "過去回をランダムにどうぞ。\n{title}\n{url}",
    "今日のランダム回はこちら。\n{title}\n{url}",
    "聴き逃し救済。いまはこれ。\n{title}\n{url}",
    "作業用BGMに：\n{title}\n{url}",
    "気分で1本。\n{title}\n{url}",
  ];

  const t = pickRandom(templates);
  return t.replace("{title}", title).replace("{url}", url);
}

async function postToX(text) {
  // X API v2: POST /2/tweets（Bearer は “ユーザーコンテキストのアクセストークン” が前提）
  const res = await fetch("https://api.twitter.com/2/tweets", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${X_BEARER_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  const bodyText = await res.text();
  if (!res.ok) {
    throw new Error(`X post failed: ${res.status} ${res.statusText}\n${bodyText}`);
  }
  return bodyText;
}

async function main() {
  if (!RSS_URL) fail("Missing env: RSS_URL");
  if (!X_BEARER_TOKEN && !DRY_RUN) fail("Missing env: X_BEARER_TOKEN");

  const rssRes = await fetch(RSS_URL, { redirect: "follow" });
  if (!rssRes.ok) {
    fail(`Failed to fetch RSS: ${rssRes.status} ${rssRes.statusText}`);
  }
  const xml = await rssRes.text();

  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "",
    // CDATA などの混在でも崩れにくくする
    parseTagValue: true,
    trimValues: true,
  });

  const data = parser.parse(xml);

  // RSS 2.0: rss.channel.item / Atom: feed.entry など、最低限の分岐
  const channel = data?.rss?.channel;
  const rssItems = asArray(channel?.item);

  const atomEntries = asArray(data?.feed?.entry);

  const items = rssItems.length ? rssItems : atomEntries;
  if (!items.length) fail("No items found in RSS/Atom feed.");

  const pool = SKIP_LATEST && items.length >= 2 ? items.slice(1) : items;
  const picked = pickRandom(pool);

  const title =
    (typeof picked?.title === "string" ? picked.title : "") ||
    (typeof picked?.["itunes:title"] === "string" ? picked["itunes:title"] : "") ||
    "（タイトル不明）";

  const url = extractSpotifyEpisodeUrl(picked);
  if (!url) fail("Could not extract episode URL from feed item.");

  const tweet = buildTweetText({ title, url });

  console.log("---- Selected episode ----");
  console.log("Title:", title);
  console.log("URL:", url);
  console.log("---- Tweet ----");
  console.log(tweet);

  if (DRY_RUN) {
    console.log("DRY_RUN=1 -> skip posting to X.");
    return;
  }

  const result = await postToX(tweet);
  console.log("Posted to X successfully.");
  console.log(result);
}

main().catch((e) => {
  console.error(e?.stack || String(e));
  process.exit(1);
});
