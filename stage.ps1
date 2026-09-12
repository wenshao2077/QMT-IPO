# Retained compatibility name. Mode and root must now be explicit.
param([Parameter(Mandatory=$true)][ValidateSet('New','Upgrade')][string]$Mode,
      [Parameter(Mandatory=$true)][string]$Root,[switch]$InstallDependencies)
& (Join-Path $PSScriptRoot 'install.ps1') -Mode $Mode -Root $Root -InstallDependencies:$InstallDependencies
exit $LASTEXITCODE
