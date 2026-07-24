# Install or update Ortur Engraver on Windows (venv, deps, .env, optional git pull).
# Usage: .\scripts\install.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Branch = "main",
    [switch]$SkipGit
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$ServerDir = Join-Path $Root "server"

if (-not (Test-Path (Join-Path $ServerDir "run.py"))) {
    Write-Error "Expected server\run.py under $Root"
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "git")) {
    Write-Error "git required — winget install --id Git.Git -e"
}
if (-not (Test-Command "python") -and -not (Test-Command "py")) {
    Write-Error "Python 3.11+ required — winget install --id Python.Python.3.12 -e"
}

Write-Host "==> Ortur Engraver install/update"
Write-Host "    $Root"

if (-not $SkipGit -and (Test-Path (Join-Path $Root ".git"))) {
    Write-Host "==> git pull ($Branch)"
    git -C $Root fetch --prune origin
    git -C $Root checkout $Branch
    git -C $Root pull --ff-only origin $Branch
}

$VenvDir = Join-Path $ServerDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "==> python venv + deps"
if (-not (Test-Path $VenvPython)) {
    if (Test-Command "python") {
        & python -m venv $VenvDir
    }
    else {
        & py -3 -m venv $VenvDir
    }
}

& $VenvPython -m pip install -q --upgrade pip
& $VenvPython -m pip install -q -r (Join-Path $ServerDir "requirements.txt")

# First-time .env only — never overwrite on updates
& (Join-Path $ScriptDir "setup-env.ps1")

Write-Host ""
Write-Host "OK. Start with:  $(Join-Path $Root 'run.ps1')"
Write-Host "Close LaserGRBL / LightBurn first (they lock the COM port)."
