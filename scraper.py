"""
scraper.py — Headless Pinterest board scraper using Playwright.

Uses API response interception (BoardFeedResource) + a pre-scroll DOM
harvest to collect board pins. The pre-scroll harvest captures SSR-rendered
pins before "More ideas" content loads.

Usage (standalone test):
    python3 scraper.py https://pinterest.com/username/board-name/
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright


SESSION_FILE = str(Path(__file__).parent / ".pinterest_session.json")
SCROLL_WAIT_MS = 1500
MAX_STABLE_ATTEMPTS = 8
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
        if "BoardFeedResource" not in response.url and "BoardSectionPinsResource" not in response.url:
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
                        api_images.add(upgrade_url(url))
                        accepted += 1
                        break
            print(f"[scraper] API batch: +{accepted} kept, {skipped} skipped — {len(api_images)} total")
        except Exception:
            pass

    async with async_playwright() as p:
        has_session = os.path.exists(SESSION_FILE)
        browser = await p.chromium.launch(headless=has_session)

        ctx_opts = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 900},
        }
        if has_session:
            ctx_opts["storage_state"] = SESSION_FILE

        context = await browser.new_context(**ctx_opts)
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

        # ── Pre-scroll harvest: grab SSR pins before "More ideas" loads ──────
        ssr_urls = await page.evaluate("""
            () => {
                const urls = [];
                const imgs = document.querySelectorAll('img[src*="i.pinimg.com"]');
                for (const img of imgs) {
                    const src = img.src || '';
                    if (src.includes('/736x/') || src.includes('/474x/') || src.includes('/236x/') || src.includes('/originals/')) {
                        urls.push(src);
                    }
                }
                return urls;
            }
        """)
        ssr_count = 0
        for u in ssr_urls:
            upgraded = upgrade_url(u)
            if upgraded not in api_images:
                api_images.add(upgraded)
                ssr_count += 1
        if ssr_count:
            print(f"[scraper] Pre-scroll harvest: +{ssr_count} SSR pins — {len(api_images)} total")

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
                # Try scrolling up then back down to trigger lazy loading
                if stable_count == 4:
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(1000)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(SCROLL_WAIT_MS)
                if stable_count >= MAX_STABLE_ATTEMPTS:
                    print("[scraper] No new pins, stopping")
                    break
            else:
                stable_count = 0
                last_count = n

        # ── Post-scroll harvest: scroll back to top and grab any remaining ────
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(2000)
        post_urls = await page.evaluate("""
            () => {
                const urls = [];
                const imgs = document.querySelectorAll('img[src*="i.pinimg.com"]');
                for (const img of imgs) {
                    const src = img.src || '';
                    if (src.includes('/736x/') || src.includes('/474x/') || src.includes('/236x/') || src.includes('/originals/')) {
                        urls.push(src);
                    }
                }
                return urls;
            }
        """)
        post_count = 0
        for u in post_urls:
            upgraded = upgrade_url(u)
            if upgraded not in api_images:
                api_images.add(upgraded)
                post_count += 1
        if post_count:
            print(f"[scraper] Post-scroll harvest: +{post_count} — {len(api_images)} total")

        # Save session for future runs
        await context.storage_state(path=SESSION_FILE)
        await browser.close()

    result = list(api_images)
    print(f"[scraper] Done. {len(result)} board pin images collected.")
    return result, board_pin_count


def get_board_slug(board_url):
    """e.g. https://pinterest.com/user/my-board/ → my-board"""
    parts = [p for p in board_url.rstrip("/").split("/") if p]
    slug = parts[-1] if parts else "pinterest-board"
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", slug)
    return slug or "pinterest-board"


async def login_to_pinterest():
    """Open a visible browser so the user can log in. Saves session for reuse."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded")

        print("[scraper] Browser opened — log in to Pinterest, then press Enter here.")
        await asyncio.get_event_loop().run_in_executor(None, input)

        await context.storage_state(path=SESSION_FILE)
        await browser.close()
        print(f"[scraper] Session saved to {SESSION_FILE}")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 scraper.py login                    — log in to Pinterest (one time)")
        print("  python3 scraper.py <pinterest-board-url>    — scrape a board")
        sys.exit(1)

    if sys.argv[1] == "login":
        asyncio.run(login_to_pinterest())
    else:
        url = sys.argv[1]
        urls, count = asyncio.run(scrape_board(url))
        print(f"\nBoard declared: {count} pins")
        print(f"Collected: {len(urls)} images")
        for u in urls[:10]:
            print(" ", u)
        if len(urls) > 10:
            print(f"  ... and {len(urls) - 10} more")
