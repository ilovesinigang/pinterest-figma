"""
scraper.py — Headless Pinterest board scraper using Playwright.

Uses API response interception (BoardFeedResource) to collect only
this board's pins. No DOM scraping — immune to "More ideas" pollution.

Known limitation: Pinterest server-renders the first ~5 pins in HTML
(no XHR fired), so those are not captured by API interception.

Usage (standalone test):
    python3 scraper.py https://pinterest.com/username/board-name/
"""

import asyncio
import re
import sys
from playwright.async_api import async_playwright


SCROLL_WAIT_MS = 1500
MAX_STABLE_ATTEMPTS = 4
MAX_SCROLL_SECONDS = 300


def upgrade_url(url):
    """Upgrade Pinterest thumbnail URL to originals resolution."""
    url = url.split("?")[0]
    url = re.sub(r"/\d+x/", "/originals/", url)
    return url


async def get_board_pin_count(page):
    """
    Read the pin count from Pinterest's embedded page JSON.
    Falls back to visible text. Returns int or 0 if not found.
    """
    try:
        count = await page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script[type="application/json"]');
                for (const s of scripts) {
                    try {
                        const str = s.textContent;
                        const m = str.match(/"pin_count":(\\d+)/);
                        if (m) return parseInt(m[1]);
                    } catch(e) {}
                }
                return null;
            }
        """)
        if count:
            return count
    except Exception:
        pass

    try:
        text = await page.evaluate("document.body.innerText")
        match = re.search(r"\b([\d,]+)\s+[Pp]ins\b", text)
        if match:
            return int(match.group(1).replace(",", ""))
    except Exception:
        pass

    return 0


def _board_slug_from_url(url):
    """Normalise a board URL to '/username/board-name/' for comparison."""
    path = re.sub(r"https?://[^/]+", "", url).lower().rstrip("/") + "/"
    return path


async def scrape_board(board_url):
    """
    Navigate to a public Pinterest board, intercept BoardFeedResource API
    responses, and return (image_url_list, board_pin_count).
    Only accepts pins whose board URL matches the requested board.
    """
    api_images = set()
    board_path = _board_slug_from_url(board_url)

    async def on_response(response):
        if "BoardFeedResource" not in response.url:
            return
        try:
            data = await response.json()
            pins = data.get("resource_response", {}).get("data", [])
            if not isinstance(pins, list):
                return
            accepted = 0
            skipped = 0
            for pin in pins:
                if not isinstance(pin, dict):
                    continue
                # Only accept pins that belong to this board
                pin_board = pin.get("board", {})
                pin_board_url = pin_board.get("url", "") if isinstance(pin_board, dict) else ""
                if pin_board_url and board_path not in _board_slug_from_url(pin_board_url):
                    skipped += 1
                    continue
                images = pin.get("images", {})
                for size in ["orig", "736x", "474x", "236x"]:
                    img = images.get(size, {})
                    url = img.get("url", "") if isinstance(img, dict) else ""
                    if url:
                        api_images.add(url)
                        accepted += 1
                        break
            print(f"[scraper] API batch: +{accepted} kept, {skipped} skipped — {len(api_images)} total")
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # Register interceptor before navigating
        page.on("response", on_response)

        print(f"[scraper] Navigating to {board_url}")
        await page.goto(board_url, wait_until="domcontentloaded", timeout=30000)

        try:
            await page.wait_for_selector('a[href*="/pin/"]', timeout=20000)
        except Exception:
            print("[scraper] Warning: pin cards didn't appear, proceeding anyway")

        # Wait for network to settle before scrolling
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            await page.wait_for_timeout(2000)

        board_pin_count = await get_board_pin_count(page)
        if board_pin_count:
            print(f"[scraper] Board declares {board_pin_count} pins")
        else:
            print("[scraper] Could not read pin count, will scroll to end")

        # ── Initial DOM harvest (pre-scroll only) ─────────────────────────────
        # Pinterest SSR-renders the first ~5 pins directly into the HTML.
        # Those never fire a BoardFeedResource XHR so the API interceptor
        # misses them. We scrape them from the DOM NOW — before any scrolling —
        # when "More ideas" is guaranteed not to be in the DOM yet.
        initial_batch = await page.evaluate("""
            () => {
                const pinLinks = document.querySelectorAll('a[href*="/pin/"]');
                const urls = [];
                pinLinks.forEach(link => {
                    const img = link.querySelector('img');
                    if (!img) return;
                    if (img.srcset) {
                        let best = '', bestW = 0;
                        img.srcset.split(',').forEach(part => {
                            const t = part.trim().split(/\\s+/);
                            if (t.length >= 2) {
                                const w = parseInt(t[1]) || 0;
                                if (w > bestW) { bestW = w; best = t[0]; }
                            } else if (t.length === 1 && !best) { best = t[0]; }
                        });
                        if (best) { urls.push(best); return; }
                    }
                    if (img.src) urls.push(img.src);
                });
                return urls;
            }
        """)
        pre_scroll_count = 0
        for url in initial_batch:
            clean = url.split("?")[0]
            if "i.pinimg.com" in clean:
                upgraded = upgrade_url(clean)
                if upgraded not in api_images:
                    api_images.add(upgraded)
                    pre_scroll_count += 1
        print(f"[scraper] Pre-scroll DOM harvest: +{pre_scroll_count} pins — {len(api_images)} total")

        # ── Scroll loop ───────────────────────────────────────────────────────
        stable_count = 0
        last_count = 0
        elapsed = 0

        while elapsed < MAX_SCROLL_SECONDS:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(SCROLL_WAIT_MS)
            elapsed += SCROLL_WAIT_MS / 1000

            n = len(api_images)
            print(f"[scraper] Scrolling… {n} pins"
                  + (f" of {board_pin_count} expected" if board_pin_count else ""))

            if board_pin_count and n >= board_pin_count:
                print("[scraper] Reached declared board count, stopping")
                break

            if n == last_count:
                stable_count += 1
                if stable_count >= MAX_STABLE_ATTEMPTS:
                    print("[scraper] No new pins, stopping")
                    break
            else:
                stable_count = 0
                last_count = n

        await browser.close()

    # Upgrade to originals resolution
    upgraded = []
    seen = set()
    for url in api_images:
        up = upgrade_url(url)
        if up not in seen:
            seen.add(up)
            upgraded.append(up)

    print(f"[scraper] Done. {len(upgraded)} board pin images collected.")
    return upgraded, board_pin_count


def get_board_slug(board_url):
    """e.g. https://pinterest.com/user/my-board/ → my-board"""
    parts = [p for p in board_url.rstrip("/").split("/") if p]
    slug = parts[-1] if parts else "pinterest-board"
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", slug)
    return slug or "pinterest-board"


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scraper.py <pinterest-board-url>")
        sys.exit(1)

    url = sys.argv[1]
    urls, count = asyncio.run(scrape_board(url))
    print(f"\nBoard declared: {count} pins")
    print(f"Collected: {len(urls)} images")
    for u in urls[:10]:
        print(" ", u)
    if len(urls) > 10:
        print(f"  ... and {len(urls) - 10} more")
