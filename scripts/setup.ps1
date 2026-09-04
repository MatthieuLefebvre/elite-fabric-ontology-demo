[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        & py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            & py -3 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer and the Python launcher.' }
        }
    }
    $python = Join-Path $root '.venv/Scripts/python.exe'
    & $python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
    if ($LASTEXITCODE -ne 0) { throw 'Existing .venv is older than Python 3.11; review before replacing.' }
    & $python -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Host 'Local environment ready. No cloud resources or credentials were configured.'
} finally {
    Pop-Location
}