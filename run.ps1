$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
& .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
