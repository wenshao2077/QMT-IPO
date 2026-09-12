# Backward-compatible 3.2 task target. Only an existing task invokes this worker.
param([Parameter(Mandatory=$true)][ValidateSet('cycle','notify','monitor','backup')][string]$Action)
$ErrorActionPreference='Stop'
$python=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -B (Join-Path $PSScriptRoot 'code/launcher.py') $Action --root $PSScriptRoot
exit $LASTEXITCODE
