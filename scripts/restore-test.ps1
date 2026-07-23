[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupDirectory)
$ErrorActionPreference = 'Stop'
$Resolved = (Resolve-Path -LiteralPath $BackupDirectory).Path
if (-not $Resolved.StartsWith('D:\Backups\LocalKnowledgePortal\database\', [StringComparison]::OrdinalIgnoreCase)) {
  throw 'BackupDirectory is outside the allowed backup tree.'
}
$Dump = Join-Path $Resolved 'lkp.dump'
$Manifest = Join-Path $Resolved 'manifest.json'
if (-not (Test-Path -LiteralPath $Dump) -or -not (Test-Path -LiteralPath $Manifest)) {
  throw 'Backup dump or manifest missing.'
}
$Expected = (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).sha256
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Dump).Hash.ToLowerInvariant()
if ($Expected -ne $Actual) { throw 'Backup checksum mismatch.' }
$Repo = Split-Path -Parent $PSScriptRoot
$TestDb = 'lkp_restore_' + (Get-Date -Format 'yyyyMMddHHmmss')
$Compose = Join-Path $Repo 'infra\docker\compose.yaml'
$EnvFile = Join-Path $Repo '.env'
$Container = docker compose --env-file $EnvFile -f $Compose ps -q postgres
if (-not $Container) { throw 'PostgreSQL container is not running.' }
$ContainerDump = "/tmp/$TestDb.dump"
docker cp $Dump "${Container}:${ContainerDump}"
docker compose --env-file $EnvFile -f $Compose `
  exec -T postgres createdb -U lkp $TestDb
try {
  docker compose --env-file $EnvFile -f $Compose exec -T postgres `
    pg_restore -U lkp -d $TestDb --exit-on-error $ContainerDump
  docker compose --env-file $EnvFile -f $Compose `
    exec -T postgres psql -U lkp -d $TestDb -v ON_ERROR_STOP=1 -c `
    'SELECT (SELECT count(*) FROM document) documents, (SELECT count(*) FROM document_chunk) chunks, (SELECT count(*) FROM chunk_embedding) vectors, (SELECT version_num FROM alembic_version) revision;'
  docker compose --env-file $EnvFile -f $Compose `
    exec -T postgres psql -U lkp -d $TestDb -v ON_ERROR_STOP=1 -c `
    'SELECT conname FROM pg_constraint WHERE contype = ''f'' AND NOT convalidated;'
  docker compose --env-file $EnvFile -f $Compose exec -T postgres psql -U lkp `
    -d $TestDb -v ON_ERROR_STOP=1 -c `
    "SELECT count(*) FROM document_chunk WHERE content ILIKE '%PostgreSQL%';"
  Write-Host "Restore test succeeded for temporary database $TestDb"
}
finally {
  docker compose --env-file $EnvFile -f $Compose `
    exec -T postgres dropdb -U lkp --if-exists $TestDb
}
