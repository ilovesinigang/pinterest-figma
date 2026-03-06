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
    if (url.pathname === '/img' || url.pathname === '//img') {
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
  const ssrImages = new Set();
  const apiImages = new Set();

  // ── Step 1: fetch board HTML for board_id + fallback SSR pins ─────────────
  const pageResp = await fetch(boardUrl, {
    headers: { ...HEADERS, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8' },
  });
  if (!pageResp.ok) throw new Error('Board page returned ' + pageResp.status + '. Is the board public?');

  const html = await pageResp.text();

  // SSR images as fallback only (includes "More ideas" contamination)
  for (const m of html.matchAll(/https:\/\/i\.pinimg\.com\/(?:736x|474x)\/[a-f0-9/]+\.[a-zA-Z]+/g)) {
    ssrImages.add(upgradeUrl(m[0]));
  }

  // Extract board_id — handle both "board_id":"123" and "board_id":123
  var boardIdMatch = html.match(/"board_id"\s*:\s*"?(\d+)"?/);
  var boardId = boardIdMatch ? boardIdMatch[1] : null;
  var pinCountMatch = html.match(/"pin_count"\s*:\s*(\d+)/);
  var pinCount = pinCountMatch ? parseInt(pinCountMatch[1]) : 0;

  if (!boardId) {
    return { urls: Array.from(ssrImages), count: pinCount, warning: 'Could not paginate — only SSR pins returned', source: 'ssr' };
  }

  // ── Step 2: paginate BoardFeedResource API ────────────────────────────────
  var bookmark = null;
  var pages = 0;

  while (pages < 25) {
    pages++;

    var options = {
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

    var apiUrl =
      'https://www.pinterest.com/resource/BoardFeedResource/get/' +
      '?source_url=' + encodeURIComponent(boardPath) +
      '&data=' + encodeURIComponent(JSON.stringify({ options: options, context: {} })) +
      '&_=' + Date.now();

    var apiResp = await fetch(apiUrl, {
      headers: {
        'User-Agent': HEADERS['User-Agent'],
        'Accept-Language': HEADERS['Accept-Language'],
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': boardUrl,
      },
    });
    if (!apiResp.ok) break;

    var data;
    try { data = await apiResp.json(); } catch (e) { break; }

    var pins = data && data.resource_response && data.resource_response.data;
    if (!Array.isArray(pins) || pins.length === 0) break;

    for (var j = 0; j < pins.length; j++) {
      var pin = pins[j];
      if (!pin || typeof pin !== 'object') continue;

      // Filter: only accept pins from this board
      var pinBoard = pin.board;
      var pinBoardUrl = (pinBoard && typeof pinBoard === 'object' && pinBoard.url) ? pinBoard.url : '';
      if (pinBoardUrl) {
        var pinPath = boardPathFrom(pinBoardUrl).toLowerCase();
        if (pinPath.indexOf(boardPath.toLowerCase().slice(1, -1)) === -1) continue;
      }

      var imgs = pin.images || {};
      var sizes = ['orig', '736x', '474x', '236x'];
      for (var k = 0; k < sizes.length; k++) {
        var img = imgs[sizes[k]];
        var u = (img && typeof img === 'object') ? img.url : '';
        if (u) { apiImages.add(upgradeUrl(u)); break; }
      }
    }

    bookmark = data && data.resource_response && data.resource_response.bookmark;
    if (!bookmark || bookmark === '-end-') break;
    if (pinCount && apiImages.size >= pinCount) break;
  }

  // If API returned results, use ONLY those (no SSR contamination)
  if (apiImages.size > 0) {
    return { urls: Array.from(apiImages), count: pinCount, source: 'api', pages: pages };
  }

  // Fallback to SSR if API returned nothing
  return { urls: Array.from(ssrImages), count: pinCount, warning: 'API returned no pins — SSR fallback', source: 'ssr' };
}
