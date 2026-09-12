# Legacy name is now a passive GUI entry. Authorization is a separate GUI click.
param([string]$Root=$PSScriptRoot)
$pythonw=Join-Path $Root '.venv/Scripts/pythonw.exe'
& $pythonw -B (Join-Path $Root 'code/panel.py') --root $Root
