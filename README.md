# DLkit

A local web app for downloading and transcribing videos from **YouTube**, **TikTok**, and **Instagram**. Built on [Flask](https://flask.palletsprojects.com/) + [yt-dlp](https://github.com/yt-dlp/yt-dlp), it runs entirely on your own machine — paste a link, pick a quality, and the file downloads straight to your browser.

> Video Downloader & Transcriber

---

## Features

- **Three platforms** — dedicated tabs for YouTube, TikTok, and Instagram.
- **Quality picker** — fetches every available resolution (144p–4K) with an estimated file size for each, and flags which ones download fast (no re-encode needed).
- **Audio-only** — extract MP3 at a selectable bitrate (64k–320k).
- **Trim before download** — set start/end timestamps to grab just a clip. On YouTube, only the needed segment is fetched.
- **Chapter detection** — chapters are surfaced so you can trim to a section.
- **TikTok watermark removal** — optional no-watermark download.
- **Transcription** — pull a transcript from a video's captions, falling back to [Whisper](https://github.com/openai/whisper) if no captions exist.
- **Batch mode** — queue a list of URLs and download them in one go (available on all three platforms).
- **Live progress** — real-time speed/ETA/percentage via server-sent events.
- **Fast downloads** — uses [aria2c](https://aria2.github.io/) for parallel connections when available, plus multi-threaded ffmpeg processing.
- **Self-updating** — checks the git remote and can pull the latest version from the UI.

---

## Requirements

- **Python 3.8+** (3.11+ recommended)
- **ffmpeg** / **ffprobe** — for merging, trimming, and re-encoding
- **aria2c** *(optional)* — for faster downloads
- **Whisper** *(optional)* — only needed to transcribe videos that have no captions (`pip install openai-whisper`)

---

## Setup (Windows — one click)

Run **`setup.bat`**. It will:

1. Check for Python 3.8+
2. Create a virtual environment
3. Install the Python dependencies (Flask, yt-dlp)
4. Download ffmpeg (via winget)
5. Create a **DLkit** shortcut on your Desktop

Then double-click the **DLkit** shortcut (or run `start.bat`) to launch.

## Setup (manual / other platforms)

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Make sure ffmpeg and ffprobe are on your PATH
#    (or drop ffmpeg.exe / ffprobe.exe into the project folder on Windows)

# 4. Run
python app.py
```

The app starts at **http://127.0.0.1:5000**.

---

## Usage

1. Open **http://127.0.0.1:5000** in your browser.
2. Pick a platform tab (YouTube / TikTok / Instagram).
3. Paste a video URL — available resolutions and file sizes load automatically.
4. Choose a resolution (or **Audio only**), optionally set trim timestamps, and click download.
5. Switch to **Batch** mode to queue multiple URLs at once.

### Private / age-restricted videos

If a video requires login, export your browser cookies to a file named **`cookies.txt`** in the project root. yt-dlp will use it automatically. This file is gitignored — keep it private, it contains session tokens.

---

## How it works

| Component | Role |
|---|---|
| `app.py` | Flask server — download/transcribe jobs, SSE progress, quality probing, auto-update |
| `templates/index.html` | Single-page UI with platform tabs, batch queue, and live progress |
| `setup.bat` / `start.bat` | Windows one-click install and launch |
| `launch.py` | Starts the dev server and opens the browser |

Downloads run on background threads and report progress over a server-sent-events stream. Finished files are streamed to the browser and their temporary working folders are cleaned up afterward. Per-platform format selection picks h264/AAC where possible for a fast stream-copy into MP4, and only re-encodes (TikTok's bytevc1/HEVC, VP9, AV1, etc.) when needed for compatibility.

---

## Notes

- Binaries (`ffmpeg.exe`, `ffprobe.exe`, `deno.exe`) and the `downloads/` folder are gitignored — supply your own.
- This tool is for downloading content you have the right to download. Respect each platform's terms of service and applicable copyright law.
