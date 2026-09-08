# Satsang Clips

Turns a long recorded session (e.g. a weekly ~2 hour satsang) into short,
ready-to-post clips for YouTube, Instagram Reels, and Twitter/X - with Claude
finding the good moments for you, so nobody has to scrub through 2 hours of
footage by hand.

**Cost:** $0/month in subscriptions. The only ongoing cost is a few cents of
Claude API usage per session (see "What this costs" below) - everything else
(transcription, video cutting/cropping/captioning) runs free and local via
open-source tools.

**Why a web app, not a desktop app:** your team is split across Mac and
Windows. Instead of building/maintaining two native apps, this runs as a
small local web server - whoever has the raw footage runs it on their
machine, and everyone else opens it in a browser (from the same machine, or
from any device on the same Wi-Fi/network) to review the transcript, adjust
clip boundaries, claim clips, and render them. One shared tool, works
identically on both platforms.

## How it works

1. **Upload** the raw session video into the app.
2. **Transcribe** - runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
   locally (free, no upload of your footage anywhere) and produces a
   timestamped transcript.
3. **Suggest clips** - sends the transcript text (not the video) to the
   Claude API, which proposes 10-20 self-contained 45-120 second clips, each
   with a suggested YouTube title, Instagram caption, tweet text, and
   hashtags.
4. **Review & assign** - your team reviews the suggested clips in the
   browser, tweaks in/out points (using the video player), edits captions,
   and assigns clips to whoever's editing them.
5. **Render** - per clip, per platform, using `ffmpeg`: cuts the segment,
   crops to 9:16 for Reels, burns in captions, and produces a file ready to
   upload.

## One-time setup

You need [Python 3.10+](https://www.python.org/downloads/) and `ffmpeg`.
Whoever will host the app (i.e. run it on their machine while working with
the raw footage) does this once:

**Mac / Linux:**
```bash
bash scripts/setup_mac_linux.sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

This creates a virtual environment and installs everything from
`requirements.txt`.

### Get a Claude API key (separate from your Claude.ai subscription)

1. Go to [console.anthropic.com](https://console.anthropic.com), sign up,
   and create an API key.
2. **Set a spend limit** under Settings -> Billing (e.g. $5/month) as a
   safety net - see "What this costs" below for why that's plenty.
3. Set it as an environment variable before running the app:
   - Mac/Linux: `export ANTHROPIC_API_KEY=sk-ant-...`
   - Windows PowerShell: `$env:ANTHROPIC_API_KEY = 'sk-ant-...'`

This is a **separate account/billing from your Claude.ai chat subscription**
- the chat app doesn't give you API access, and the API doesn't require a
Claude.ai subscription.

## Running it

**Mac/Linux:** `bash scripts/run.sh`
**Windows:** `powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1`

Then open **http://localhost:8000**. To let teammates on other machines (Mac
or Windows, doesn't matter) reach it over your local network, find your
machine's LAN IP (`ipconfig` on Windows / `ifconfig` or `ipconfig getifaddr en0`
on Mac) and share `http://<that-ip>:8000` - they just open it in a browser,
nothing to install.

## What this costs

Transcription and video rendering are free (local, open-source). The only
paid step is asking Claude to suggest clips from the transcript:

| | Per session (~2 hrs) | 5 sessions/month |
|---|---|---|
| Claude Sonnet 5 (default) | ≈ $0.10 | ≈ $0.50 |
| Claude Opus 5 (higher quality, costs more) | ≈ $0.25 | ≈ $1.25 |

Change the model via `SATSANG_CLAUDE_MODEL` (defaults to `claude-sonnet-5`).
Even with a generous buffer for retries or longer sessions, expect well
under $3/month.

## Storage note

Raw 2-hour session videos are large (tens of GB at high quality). Everything
lives under `data/` (already gitignored - never commit it). If several
people need the raw footage, don't rely on free-tier cloud storage for
it - a directly-synced folder ([Syncthing](https://syncthing.net), free and
cross-platform) or a shared external drive works better for files this size.
The rendered *output* clips are small (a few MB each) and fine to share
however you like.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | - | required for the "Suggest clips" step |
| `SATSANG_CLAUDE_MODEL` | `claude-sonnet-5` | model used for clip suggestions |
| `WHISPER_MODEL_SIZE` | `small` | faster-whisper model size (`base`/`small` for CPU laptops; `medium`/`large-v3` if you have a GPU) |
| `WHISPER_CPU_THREADS` | (cores - 1) | CPU threads faster-whisper uses. It doesn't auto-detect this well, so we default to nearly all your cores - lower it if transcription is starving other apps |
| `SATSANG_DATA_DIR` | `./data` | where sessions/transcripts/clips are stored |

## Project layout

```
backend/       FastAPI app (main.py), session store, transcription/
                clip-suggestion/rendering services
frontend/       Plain HTML/CSS/JS UI served by the backend - no build step
scripts/        Setup/run scripts for Mac and Windows, plus a synthetic
                test-video generator (make_test_clip.sh) for smoke testing
                without real footage
tests/          Pipeline smoke test (pytest)
data/           Runtime storage (gitignored) - one folder per session
```

## Running the tests

```bash
source .venv/bin/activate   # or the Windows equivalent
bash scripts/make_test_clip.sh /tmp/test_session.mp4   # needs espeak-ng
SATSANG_TEST_VIDEO=/tmp/test_session.mp4 pytest tests/ -v
```

The test fakes the Whisper and Claude network calls (so it runs offline) but
exercises real `ffmpeg` cutting, vertical cropping, and caption burn-in.
