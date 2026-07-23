[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'stop-codex-capture.ps1')
docker compose --env-file (Join-Path $Repo '.env') `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') stop
