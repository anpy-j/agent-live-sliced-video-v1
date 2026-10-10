$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python scripts/build_desktop.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
