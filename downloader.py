"""
downloader.py — Downloads images from a list of URLs to a local folder.

Yields progress dicts for SSE streaming:
    {"status": "downloading", "current": 24, "total": 80, "filename": "abc.jpg"}
    {"status": "done", "downloaded": 78, "skipped": 2, "failed": 0, "folder": "/path"}
    {"status": "error", "message": "..."}
"""

import os
import re
from pathlib import Path
from typing import Generator
import requests


DOWNLOAD_TIMEOUT = 15  # seconds per image
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.pinterest.com/",
}


def url_to_filename(url: str) -> str:
    """Derive a clean filename from a URL."""
    path = url.split("?")[0].rstrip("/")
    name = path.split("/")[-1]
    # Ensure it has an image extension
    if not re.search(r"\.(jpg|jpeg|png|gif|webp)$", name, re.IGNORECASE):
        name = name + ".jpg"
    return name


def fallback_url(url: str) -> str:
    """If originals/ URL fails, try 736x/ instead."""
    return re.sub(r"/originals/", "/736x/", url)


def download_images(
    urls: list[str],
    output_folder: str,
) -> Generator[dict, None, None]:
    """
    Download each URL to output_folder. Yields progress dicts.
    Skips files that already exist (dedup by filename).
    """
    folder = Path(output_folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)

    total = len(urls)
    downloaded = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(urls, start=1):
        filename = url_to_filename(url)
        dest = folder / filename

        # Skip if already downloaded
        if dest.exists():
            skipped += 1
            yield {
                "status": "downloading",
                "current": i,
                "total": total,
                "filename": filename,
                "skipped": True,
            }
            continue

        # Try downloading (originals first, then 736x fallback)
        success = False
        for attempt_url in [url, fallback_url(url)]:
            try:
                resp = requests.get(
                    attempt_url,
                    headers=HEADERS,
                    timeout=DOWNLOAD_TIMEOUT,
                    stream=True,
                )
                if resp.status_code == 200:
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded += 1
                    success = True
                    break
            except requests.RequestException:
                continue

        if not success:
            failed += 1

        yield {
            "status": "downloading",
            "current": i,
            "total": total,
            "filename": filename,
            "skipped": False,
            "success": success,
        }

    yield {
        "status": "done",
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "folder": str(folder),
    }
