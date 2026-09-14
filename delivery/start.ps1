# ASCII launcher compatible with Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'setup.ps1') -Operation Menu
