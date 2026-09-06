#!/usr/bin/env bash
# Launch the app on Mac/Linux. Run from the repo root: bash scripts/run.sh
set -euo pipefail
source .venv/bin/activate
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
