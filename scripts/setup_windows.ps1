# One-time setup on Windows. Run from the repo root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "ffmpeg not found."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Installing with winget..."
    winget install --id Gyan.FFmpeg -e
    Write-Host "Restart this terminal so PATH picks up ffmpeg, then re-run this script."
    exit 1
  } else {
    Write-Host "Install ffmpeg manually: https://www.gyan.dev/ffmpeg/builds/ (add it to PATH), then re-run this script."
    exit 1
  }
}

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

Write-Host ""
Write-Host "Setup done. Next:"
Write-Host "  1. `$env:ANTHROPIC_API_KEY = 'sk-ant-...'   (from console.anthropic.com)"
Write-Host "  2. powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1"
Write-Host "  3. Open http://localhost:8000 in your browser"
