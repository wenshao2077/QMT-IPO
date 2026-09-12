param([string]$Root=$PSScriptRoot)
& (Join-Path $Root '.venv/Scripts/python.exe') -B (Join-Path $Root 'code/app.py') status --config (Join-Path $Root 'config.json')
exit $LASTEXITCODE
