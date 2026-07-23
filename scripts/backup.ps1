[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Repo '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) { throw 'Run bootstrap.ps1 first.' }
Get-Content -LiteralPath $EnvFile | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { Set-Item -Path ('Env:' + $Matches[1]) -Value $Matches[2] }
}
$Stamp = Get-Date -Format 'yyyy-MM-ddTHHmmss'
$Destination = Join-Path $env:LKP_BACKUP_DIR ('database\' + $Stamp)
if (Test-Path -LiteralPath $Destination) { throw "Backup target already exists: $Destination" }
New-Item -ItemType Directory -Path $Destination | Out-Null
$Dump = Join-Path $Destination 'lkp.dump'
$Container = docker compose --env-file $EnvFile `
  -f (Join-Path $Repo 'infra\docker\compose.yaml') ps -q postgres
if (-not $Container) { throw 'PostgreSQL container is not running.' }
$ContainerDump = "/tmp/lkp-$Stamp.dump"
docker compose --env-file $EnvFile -f (Join-Path $Repo 'infra\docker\compose.yaml') `
  exec -T postgres pg_dump -Fc -U $env:POSTGRES_USER -d $env:POSTGRES_DB `
  -f $ContainerDump
docker cp "${Container}:${ContainerDump}" $Dump
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Dump).Hash.ToLowerInvariant()
$Revision = uv run --project $Repo alembic current
$SourceHash = (Get-FileHash -Algorithm SHA256 `
  -LiteralPath (Join-Path $Repo 'config\source-roots.yaml')).Hash.ToLowerInvariant()
$Manifest = [ordered]@{
  created_at = (Get-Date).ToUniversalTime().ToString('o')
  database_version = '18.4'
  schema_revision = "$Revision"
  pgvector_version = '0.8.2'
  file_size = (Get-Item -LiteralPath $Dump).Length
  sha256 = $Hash
  source_configuration_hash = $SourceHash
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'manifest.json') `
  -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $Repo 'config\source-roots.yaml') `
  -Destination (Join-Path $Destination 'source-roots.yaml')
Write-Host "Created immutable backup: $Destination"
