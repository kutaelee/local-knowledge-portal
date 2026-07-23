[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
docker compose --env-file (Join-Path $Repo '.env') `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') up -d postgres
uv run --project $Repo alembic upgrade head
& (Join-Path $PSScriptRoot 'start-hook-collector.ps1')
& (Join-Path $PSScriptRoot 'start-indexer.ps1')
Write-Host 'PostgreSQL is ready, migrations are current, and collectors/indexer are running.'
