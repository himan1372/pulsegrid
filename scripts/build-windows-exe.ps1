# Build a standalone Pulsegrid executable on Windows 10/11 (64-bit).
#
# Prerequisites (install once):
#   - Python 3.10+ from https://www.python.org (check "Add python.exe to PATH")
#   - Rust from https://rustup.rs (accept the default VS Build Tools install)
#
# Usage (PowerShell, from the repo root):
#   .\scripts\build-windows-exe.ps1
#
# Output: dist\pulsegrid.exe — a single file, no console window.
# Double-click it to launch Pulsegrid.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

Write-Host "==> Creating virtual environment..."
py -m venv .venv
& .\.venv\Scripts\python -m pip install --upgrade pip
& .\.venv\Scripts\pip install maturin pyinstaller

Write-Host "==> Building the Rust audio engine..."
& .\.venv\Scripts\maturin develop --release

Write-Host "==> Bundling the executable..."
@"
from daw.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
"@ | Out-File -Encoding ascii "$env:TEMP\pulsegrid_entry.py"

& .\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed `
    --name pulsegrid `
    --paths python `
    --distpath "$Root\dist" `
    --workpath "$env:TEMP\pulsegrid_build" `
    --specpath "$env:TEMP\pulsegrid_build" `
    "$env:TEMP\pulsegrid_entry.py"

Write-Host ""
Write-Host "Done: $Root\dist\pulsegrid.exe"
