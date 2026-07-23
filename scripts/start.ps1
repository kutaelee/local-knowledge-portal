[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
docker compose --env-file (Join-Path $Repo '.env') `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') up -d postgres
uv run --project $Repo alembic upgrade head
Write-Host 'PostgreSQL is ready and migrations are current.'
