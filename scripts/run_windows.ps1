# Launch the app on Windows. Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1
.\.venv\Scripts\Activate.ps1
Set-Location backend
uvicorn main:app --host 0.0.0.0 --port 8000
