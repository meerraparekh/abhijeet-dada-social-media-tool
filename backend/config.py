"""Shared configuration and paths."""
import os
from pathlib import Path

# Everything lives under data/, which is gitignored and safe to wipe between weeks.
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SATSANG_DATA_DIR", REPO_ROOT / "data"))
SESSIONS_DIR = DATA_DIR / "sessions"

# faster-whisper model size. "base"/"small" are usable on a laptop CPU;
# "medium"/"large-v3" need a decent GPU or a lot of patience.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
# faster-whisper defaults to a conservative thread count on CPU (it does not
# use all cores automatically), which badly under-uses modern multi-core
# laptops - explicitly hand it (most of) the machine's logical cores.
WHISPER_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", max(1, (os.cpu_count() or 4) - 1)))

# Claude model used to turn a transcript into clip suggestions.
CLAUDE_MODEL = os.environ.get("SATSANG_CLAUDE_MODEL", "claude-sonnet-5")

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
