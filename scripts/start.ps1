[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
docker compose --env-file (Join-Path $Repo '.env') `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') up -d postgres
uv run --project $Repo alembic upgrade head
& (Join-Path $PSScriptRoot 'start-codex-capture.ps1') -Index
Write-Host 'PostgreSQL is ready, migrations are current, and Codex capture is running.'
