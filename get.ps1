# One-shot install + start for Windows.
# Usage:
#   irm https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.ps1 | iex
# Or:
#   powershell -ExecutionPolicy Bypass -File .\get.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Dir = (Join-Path $HOME "ortur_laser"),
    [string]$Branch = "main",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$RepoHttps = "https://github.com/PrawWarp/ortur_laser.git"

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "==> Ortur Engraver — easy install"
Write-Host "    target: $Dir"

if (-not (Test-Command "git")) {
    Write-Host ""
    Write-Host "Git is required. Install, then re-run:"
    Write-Host "  winget install --id Git.Git -e"
    Write-Host "https://git-scm.com/download/win"
    exit 1
}

$hasPython = $false
if (Test-Command "python") {
    try {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -eq 0) { $hasPython = $true }
    }
    catch { }
}
if (-not $hasPython -and (Test-Command "py")) {
    try {
        & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -eq 0) { $hasPython = $true }
    }
    catch { }
}
if (-not $hasPython) {
    Write-Host ""
    Write-Host "Python 3.10+ is required. Install, then re-run:"
    Write-Host "  winget install --id Python.Python.3.12 -e"
    Write-Host "https://www.python.org/downloads/  (enable 'Add python.exe to PATH')"
    exit 1
}

if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "==> updating existing clone"
    git -C $Dir fetch --prune origin
    git -C $Dir checkout $Branch
    git -C $Dir pull --ff-only origin $Branch
}
elseif (Test-Path $Dir) {
    Write-Error "$Dir exists but is not a git repo. Pass -Dir to pick another folder."
}
else {
    Write-Host "==> cloning"
    git clone --branch $Branch $RepoHttps $Dir
}

& (Join-Path $Dir "scripts\install.ps1") -Branch $Branch -SkipGit

if ($NoStart) {
    Write-Host ""
    Write-Host "Installed. Start anytime with:"
    Write-Host "  $(Join-Path $Dir 'run.ps1')"
    return
}

Write-Host ""
Write-Host "==> starting UI (Ctrl+C to stop)"
Write-Host "    http://127.0.0.1:8000"
& (Join-Path $Dir "run.ps1")
