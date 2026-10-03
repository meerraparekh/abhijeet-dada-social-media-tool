# Satsang Clips

Turns a long recorded session (e.g. a weekly ~2 hour satsang) into
ready-to-post vertical clips, full bilingual transcripts, and blog posts -
with Claude finding the good moments and writing the derived content for
you, so nobody has to scrub through 2 hours of footage by hand.

- Handles up to three synchronized camera **angles** of the same recording
  (e.g. 1x/2x/4x), auto-aligning them by audio even when they have different
  start times or one stopped recording early.
- Finds every story/parable/Q&A moment worth clipping, not just a handful -
  a dense ~2 hour session commonly yields 20-40+ clips.
- Every clip renders as one vertical 9:16 format, with burned-in English
  captions, ready for YouTube Shorts, Reels, or Stories alike.
- Full-session transcripts (Hindi original + English translation) export as
  `.srt` and Word documents.
- Blog posts: several shorter, topic-based English posts generated strictly
  from what was actually said - nothing invented.

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

1. **Upload** the raw session video - this becomes the session's *primary
   angle*, and every timestamp (transcript, clips) is anchored to its
   timeline. Optionally **add more angles** (other cameras of the same
   recording); each gets auto-synced against the primary by cross-correlating
   their audio, so you don't need to manually figure out the time offset
   between cameras that started at different moments.
2. **Transcribe** - runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
   locally on the primary angle (free, no upload of your footage anywhere)
   and produces a timestamped transcript.
3. **Translate captions** - asks Claude to translate the transcript to
   English, used for burned-in captions aimed at a wider audience. Also
   produces a readable, sentence-level English transcript export.
4. **Suggest clips** - sends the transcript text (not the video) to the
   Claude API, which finds every self-contained 45-120 second story/parable/
   Q&A moment in the session (not capped to a small handful), each with a
   suggested YouTube title, Instagram caption, tweet text, hashtags, and
   internal topic tags (reused later for blog posts).
5. **Review & assign** - your team reviews the suggested clips in the
   browser, tweaks in/out points (using the video player, on any angle),
   edits captions, and assigns clips to whoever's editing them.
6. **Render** - per clip, per angle, using `ffmpeg`: cuts the segment (from
   that angle's own footage, with the sync offset applied automatically),
   crops to vertical 9:16, burns in English captions, and produces a file
   ready to upload.
7. **Generate blog posts** (optional) - asks Claude to restructure the
   session's actual content into several shorter, topic-based English blog
   posts - never adding material that wasn't actually said - exported as one
   Word document.

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

Transcription, audio sync, silence removal, and video rendering are all free
(local, open-source). A few things call the Claude API: suggesting clips
(reads the transcript, picks moments - now finding more of them, so this
costs a bit more than before), translating the transcript to English for
burned-in captions and the English transcript export (reads and rewrites
essentially the whole transcript), generating blog posts (reads the whole
transcript and writes several full posts, the largest single call), and -
only if you turn on "Remove filler words & mistakes" when rendering a clip -
a small per-clip call to flag filler words/false starts:

| | Per session (~2 hrs) | 5 sessions/month |
|---|---|---|
| Suggest clips (Sonnet 5) | ≈ $0.15 | ≈ $0.75 |
| Translate captions (Sonnet 5) | ≈ $0.30 | ≈ $1.50 |
| Generate blog posts (Sonnet 5) | ≈ $0.35 | ≈ $1.75 |
| **Total (Sonnet 5, default)** | **≈ $0.80** | **≈ $4.00** |
| Total on Claude Opus 5 (higher quality, costs more) | ≈ $2.00 | ≈ $10.00 |
| Remove filler words & mistakes, if used (per clip, not per session) | ≈ $0.01-0.02 | negligible even used on every clip |

Change the model via `SATSANG_CLAUDE_MODEL` (defaults to `claude-sonnet-5`).
Even with a generous buffer for retries or longer sessions, expect well
under $5/month on the default model.

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
| `WHISPER_MODEL_SIZE` | `base` | faster-whisper model size. `base` comfortably beats real-time on a modern CPU; `small` is more accurate but real-world testing showed it running *slower than real-time* even after the thread/decoding tuning below - only use it if `base`'s accuracy isn't good enough for you; `medium`/`large-v3` need a decent GPU |
| `WHISPER_LANGUAGE` | `hi` | Forces transcription language. Hindi and Urdu are close to the same spoken language but use different scripts, and Whisper's auto-detection can guess wrong between them (it did, in testing) - forcing `hi` avoids that. Set to empty (`WHISPER_LANGUAGE=`) to go back to auto-detection for a different/mixed-language session |
| `WHISPER_CPU_THREADS` | (cores - 1) | CPU threads faster-whisper uses. It doesn't auto-detect this well, so we default to nearly all your cores - lower it if transcription is starving other apps |
| `WHISPER_BEAM_SIZE` | `1` (greedy) | Whisper's decoding is largely sequential and is the real CPU bottleneck, not thread count - greedy decoding (1) is several times faster than the library default of 5, at a small, usually unnoticeable accuracy cost for this use case. Raise it (e.g. `5`) only if you notice the transcript quality actually suffering |
| `SATSANG_DATA_DIR` | `./data` | where sessions/transcripts/clips are stored |

## Project layout

```
backend/       FastAPI app (main.py), session store, transcription/
                sync/clip-suggestion/rendering/blog-writing/docx-export
                services
frontend/       Plain HTML/CSS/JS UI served by the backend - no build step
scripts/        Setup/run scripts for Mac and Windows, plus a synthetic
                test-video generator (make_test_clip.sh) for smoke testing
                without real footage
tests/          Pipeline smoke test + unit tests (pytest)
data/           Runtime storage (gitignored) - one folder per session
```

## Running the tests

```bash
source .venv/bin/activate   # or the Windows equivalent
bash scripts/make_test_clip.sh /tmp/test_session.mp4   # needs espeak-ng
SATSANG_TEST_VIDEO=/tmp/test_session.mp4 pytest tests/ -v
```

The pipeline test fakes the Whisper and Claude network calls (so it runs
offline) but exercises real `ffmpeg` cutting, vertical cropping, and caption
burn-in. `tests/test_sync.py` locks down the audio cross-correlation sign
convention against synthetic signals with a known, constructed offset -
important since a flipped sign there would silently cut the wrong footage
for every non-primary angle. `tests/test_multi_angle.py` exercises offset
application (and the "angle stopped recording early" clamp/reject paths)
against real ffmpeg output.
