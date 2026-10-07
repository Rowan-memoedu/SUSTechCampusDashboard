param(
    [string]$Python = 'D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$renewScript = Join-Path $PSScriptRoot 'renew_feed.py'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Publisher Python is missing.' }
if (-not (Test-Path -LiteralPath $renewScript -PathType Leaf)) { throw 'Renewal script is missing.' }
# The signing keys remain protected by this Windows user's DPAPI credentials.
# No Codex, interactive browser, campus account, or client process is involved.
& $Python $renewScript --force
exit $LASTEXITCODE
