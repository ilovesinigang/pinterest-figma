"""
server.py — Flask server for the Pinterest → Figma downloader tool.

Endpoints:
    GET  /                → serve frontend/index.html
    GET  /scan-stream     → SSE: scrape board, cache results, emit "ready" event
    GET  /download-stream → SSE: download cached images with live progress

Run:
    python3 server.py
"""

import asyncio
import json
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, Response, request, send_from_directory

from scraper import get_board_slug, scrape_board
from downloader import download_images

app = Flask(__name__, static_folder=None)

FRONTEND_DIR = Path(__file__).parent / "frontend"
DOWNLOADS_BASE = Path.home() / "Downloads"

# In-memory cache: board_url → list of image URLs
# Populated by /scan-stream, consumed by /download-stream
_scan_cache = {}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/scan-stream")
def scan_stream():
    """
    Phase 1: scrape the board and cache results.
    Emits progress during scraping, then a "ready" event when done.
    """
    board_url = request.args.get("url", "").strip()

    if not board_url:
        def err():
            yield _sse({"status": "error", "message": "No URL provided."})
        return Response(err(), mimetype="text/event-stream")

    def event_stream():
        yield _sse({"status": "scraping", "message": "Loading board and collecting image URLs…"})

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            image_urls, board_pin_count = loop.run_until_complete(scrape_board(board_url))
            loop.close()
        except Exception as e:
            yield _sse({"status": "error", "message": f"Scraping failed: {str(e)}"})
            return

        if not image_urls:
            yield _sse({"status": "error", "message": "No images found. Is the board public?"})
            return

        # Cache for the download phase
        _scan_cache[board_url] = image_urls

        slug = get_board_slug(board_url)
        folder_display = f"~/Downloads/pinterest-{slug}/"

        yield _sse({
            "status": "ready",
            "count": len(image_urls),
            "board_count": board_pin_count,
            "folder": folder_display,
        })

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download-stream")
def download_stream():
    """
    Phase 2: download the cached images with live progress.
    Falls back to re-scraping if cache is missing.
    """
    board_url = request.args.get("url", "").strip()

    def event_stream():
        image_urls = _scan_cache.pop(board_url, None)

        if image_urls is None:
            # Cache miss — re-scrape (shouldn't normally happen)
            yield _sse({"status": "scraping", "message": "Re-scanning board…"})
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                image_urls, _ = loop.run_until_complete(scrape_board(board_url))
                loop.close()
            except Exception as e:
                yield _sse({"status": "error", "message": f"Failed: {str(e)}"})
                return

        slug = get_board_slug(board_url)
        output_folder = str(DOWNLOADS_BASE / f"pinterest-{slug}")

        for progress in download_images(image_urls, output_folder):
            yield _sse(progress)
            time.sleep(0.01)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ── Startup ───────────────────────────────────────────────────────────────────

def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://localhost:5001")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    print("★  Pinterest → Figma tool running at http://localhost:5001")
    app.run(host="localhost", port=5001, debug=False, threaded=True)
