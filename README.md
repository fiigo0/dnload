# Dnlod

Standalone macOS desktop app — paste a YouTube URL, download the video (MP4) or audio (MP3), and search for the song's lyrics.

## Prerequisites

- macOS with [Homebrew](https://brew.sh)
- Python 3.10+ (`brew install python` if needed)
- ffmpeg (`brew install ffmpeg`)

## Install

```bash
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python dnlod.py
```

## Usage

1. Paste a YouTube URL and click **Fetch** to load the title and auto-fill Artist/Song.
2. Click **Download Video (MP4)** or **Download Audio (MP3)**. Files land in `downloads/` by default (change with **Browse…** or in **Settings**).
3. Edit Artist/Song if needed and click **Search Lyrics**.
   - Default source: **lyrics.ovh** (no setup).
   - **Genius**: open **⚙ Settings**, paste a token from <https://genius.com/api-clients>, save. The Genius radio becomes enabled.

## Files

- `dnlod.py` — the app
- `requirements.txt` — Python dependencies
- `config.json` — generated on first run (Genius token, default output dir)
- `downloads/` — default output folder

## Notes

- MP3 conversion requires `ffmpeg` on `PATH`.
- For personal use; respect YouTube's Terms of Service and copyright law.
