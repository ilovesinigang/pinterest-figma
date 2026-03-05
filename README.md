# Pinterest → Figma Image Downloader

Downloads all images from a public Pinterest board to your Downloads folder.

## Requirements

- Python 3.9+
- A **public** Pinterest board (the scraper runs without login)

## Setup

```bash
# 1. Clone / navigate to this folder
cd ~/codingprojects/pinterest-figma

# 2. Install Python dependencies
pip3 install -r requirements.txt

# 3. Install Chromium (one-time, ~100MB)
python3 -m playwright install chromium

# 4. Start the tool
python3 server.py
```

Your browser will open automatically at `http://localhost:5000`.

## Usage

1. Make sure your Pinterest board is **set to Public**
2. Copy the board URL (e.g. `https://pinterest.com/username/board-name/`)
3. Paste it into the tool and click **Download Images**
4. Images are saved to `~/Downloads/{board-name}/`

## Notes

- Boards with 100–500 images are supported; scraping may take 1–3 minutes
- Duplicate filenames are skipped automatically
- Full-resolution images are downloaded where available (falls back to 736px)
