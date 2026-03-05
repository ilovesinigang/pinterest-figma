/**
 * Pinterest → Figma — Cloudflare Worker
 *
 * Acts as a CORS proxy + scraper between the Figma plugin and Pinterest.
 * Fetches a public Pinterest board and returns image URLs as JSON.
 *
 * Endpoints:
 *   GET /?url=<board-url>          → { urls: [...], count: N }
 *   GET /img?url=<image-url>       → proxied image bytes (if CDN blocks CORS)
 */

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9',
};

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') return cors(new Response(null, { status: 204 }));

    const url = new URL(request.url);

    // Image proxy endpoint — fallback if CDN blocks direct fetch from plugin
    if (url.pathname === '/img') {
      const imgUrl = url.searchParams.get('url');
      if (!imgUrl) return cors(new Response('Missing url', { status: 400 }));
      const resp = await fetch(imgUrl, { headers: HEADERS });
      const body = await resp.arrayBuffer();
      return cors(new Response(body, {
        headers: { 'Content-Type': resp.headers.get('Content-Type') || 'image/jpeg' },
      }));
    }

    // Main scrape endpoint
    const boardUrl = url.searchParams.get('url');
    if (!boardUrl) return cors(new Response(JSON.stringify({ error: 'Missing url' }), { status: 400, headers: json }));

    try {
      const result = await scrapeBoard(boardUrl);
      return cors(new Response(JSON.stringify(result), { headers: json }));
    } catch (err) {
      return cors(new Response(JSON.stringify({ error: err.message }), { status: 500, headers: json }));
    }
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const json = { 'Content-Type': 'application/json' };

function cors(response) {
  response.headers.set('Access-Control-Allow-Origin', '*');
  response.headers.set('Access-Control-Allow-Methods', 'GET, OPTIONS');
  return response;
}

function upgradeUrl(url) {
  // Use 736x — sufficient for Figma moodboarding and small enough to
  // transfer reliably through Figma's plugin message system (~100–300KB).
  return url.split('?')[0].replace(/\/\d+x\//, '/736x/');
}

function boardPathFrom(url) {
  try { return new URL(url).pathname.replace(/\/$/, '') + '/'; }
  catch { return '/' + url.replace(/^\//, ''); }
}

// ── Scraper ───────────────────────────────────────────────────────────────────

async function scrapeBoard(boardUrl) {
  if (!boardUrl.startsWith('http')) boardUrl = 'https://www.pinterest.com' + boardUrl;
  const boardPath = boardPathFrom(boardUrl);
  const images = new Set();

  // ── Step 1: fetch board HTML for SSR pins + board_id ──────────────────────
  const pageResp = await fetch(boardUrl, {
    headers: { ...HEADERS, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8' },
  });
  if (!pageResp.ok) throw new Error(`Board page returned ${pageResp.status}. Is the board public?`);

  const html = await pageResp.text();

  // Extract image URLs embedded in SSR HTML (474x and 736x are pin-specific sizes)
  for (const m of html.matchAll(/https:\/\/i\.pinimg\.com\/(?:736x|474x)\/[a-f0-9/]+\.[a-zA-Z]+/g)) {
    images.add(upgradeUrl(m[0]));
  }

  // Extract board_id and declared pin count
  const boardId = html.match(/"board_id"\s*:\s*"(\d+)"/)?.[1];
  const pinCount = parseInt(html.match(/"pin_count"\s*:\s*(\d+)/)?.[1] || '0');

  if (!boardId) {
    return { urls: [...images], count: pinCount, warning: 'Could not paginate — only SSR pins returned' };
  }

  // ── Step 2: paginate BoardFeedResource API ────────────────────────────────
  let bookmark = null;
  let pages = 0;

  while (pages < 25) {
    pages++;

    const options = {
      board_id: boardId,
      board_url: boardPath,
      page_size: 25,
      prepend: false,
      access: [],
      field_set_key: 'react_grid_pin',
      filter_section_pins: true,
      sort: 'default',
      layout: 'default',
      redux_normalize_feed: true,
    };
    if (bookmark) options.bookmarks = [bookmark];

    const apiUrl =
      `https://www.pinterest.com/resource/BoardFeedResource/get/` +
      `?source_url=${encodeURIComponent(boardPath)}` +
      `&data=${encodeURIComponent(JSON.stringify({ options, context: {} }))}` +
      `&_=${Date.now()}`;

    const apiResp = await fetch(apiUrl, {
      headers: {
        ...HEADERS,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': boardUrl,
      },
    });
    if (!apiResp.ok) break;

    let data;
    try { data = await apiResp.json(); } catch { break; }

    const pins = data?.resource_response?.data;
    if (!Array.isArray(pins) || pins.length === 0) break;

    for (const pin of pins) {
      if (!pin || typeof pin !== 'object') continue;

      // Filter: only accept pins from this board
      const pinBoardUrl = pin?.board?.url || '';
      if (pinBoardUrl) {
        const pinPath = boardPathFrom(pinBoardUrl).toLowerCase();
        if (!pinPath.includes(boardPath.toLowerCase().slice(1, -1))) continue;
      }

      const imgs = pin.images || {};
      for (const size of ['orig', '736x', '474x', '236x']) {
        const u = imgs[size]?.url;
        if (u) { images.add(upgradeUrl(u)); break; }
      }
    }

    bookmark = data?.resource_response?.bookmark;
    if (!bookmark || bookmark === '-end-') break;
    if (pinCount && images.size >= pinCount) break;
  }

  return { urls: [...images], count: pinCount };
}
