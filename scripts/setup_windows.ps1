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

# Prefer a Python version known to have ready-made (prebuilt) packages for every
# dependency here. Very new Python versions (e.g. 3.14) often lack prebuilt
# wheels for libraries like pydantic-core, which then fails to install unless
# you have a C/Rust compiler toolchain - avoid that entirely by targeting 3.12.
$pythonCmd = $null
foreach ($candidate in @("3.12", "3.11", "3.13", "3.10")) {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    py -$candidate --version *> $null
    if ($LASTEXITCODE -eq 0) { $pythonCmd = @("py", "-$candidate"); break }
  }
}
if (-not $pythonCmd) {
  Write-Host "No Python 3.10-3.13 found via the 'py' launcher."
  Write-Host "Install Python 3.12 from https://www.python.org/downloads/release/python-3120/"
  Write-Host "(check 'Add python.exe to PATH' during install), then re-run this script."
  exit 1
}
Write-Host "Using Python: $($pythonCmd -join ' ')"

& $pythonCmd[0] $pythonCmd[1] -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

Write-Host ""
Write-Host "Setup done. Next:"
Write-Host "  1. `$env:ANTHROPIC_API_KEY = 'sk-ant-...'   (from console.anthropic.com)"
Write-Host "  2. powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1"
Write-Host "  3. Open http://localhost:8000 in your browser"
