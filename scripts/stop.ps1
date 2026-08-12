[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'stop-codex-capture.ps1')
& (Join-Path $PSScriptRoot 'stop-hook-collector.ps1')
& (Join-Path $PSScriptRoot 'stop-indexer.ps1')
docker compose --env-file (Join-Path $Repo '.env') `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') stop
