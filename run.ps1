# Ensure deps are installed, then start the Ortur Engraver UI.
# Usage: .\run.ps1
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Server = Join-Path $Root "server"
$VenvPy = Join-Path $Server ".venv\Scripts\python.exe"
$Install = Join-Path $Root "scripts\install.ps1"

if (-not (Test-Path $VenvPy)) {
    Write-Host "==> first run — installing…"
    & $Install -SkipGit
}

Set-Location $Server
& $VenvPy run.py
