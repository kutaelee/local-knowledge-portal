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
$ConfigDestination = Join-Path $env:LKP_BACKUP_DIR ('config\' + $Stamp)
$VaultDestination = Join-Path $env:LKP_BACKUP_DIR ('vault\' + $Stamp)
$ManifestDestination = Join-Path $env:LKP_BACKUP_DIR ('manifests\' + $Stamp)
foreach ($Target in @($Destination,$ConfigDestination,$VaultDestination,$ManifestDestination)) {
  if (Test-Path -LiteralPath $Target) { throw "Backup target already exists: $Target" }
  New-Item -ItemType Directory -Path $Target | Out-Null
}
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
$OllamaModel = $null
try {
  $Tags = Invoke-RestMethod -Uri ($env:LKP_OLLAMA_BASE_URL + '/api/tags') -TimeoutSec 5
  $OllamaModel = $Tags.models | Where-Object { $_.name -eq $env:LKP_EMBEDDING_MODEL } |
    Select-Object -First 1
} catch {}
$Manifest = [ordered]@{
  created_at = (Get-Date).ToUniversalTime().ToString('o')
  database_version = '18.4'
  schema_revision = "$Revision"
  pgvector_version = '0.8.2'
  file_size = (Get-Item -LiteralPath $Dump).Length
  sha256 = $Hash
  source_configuration_hash = $SourceHash
  embedding = [ordered]@{
    provider = $env:LKP_EMBEDDING_PROVIDER
    model = $env:LKP_EMBEDDING_MODEL
    digest = $env:LKP_EMBEDDING_MODEL_DIGEST
    dimension = [int]$env:LKP_EMBEDDING_DIMENSION
    revision = $env:LKP_EMBEDDING_REVISION
    installed_size = if ($OllamaModel) { $OllamaModel.size } else { $null }
  }
  pipeline_version = '1.0.0'
  event_spool = [ordered]@{
    source = $env:LKP_HOOK_SPOOL_DIR
    files = if (Test-Path -LiteralPath $env:LKP_HOOK_SPOOL_DIR) {
      (Get-ChildItem -LiteralPath $env:LKP_HOOK_SPOOL_DIR -Recurse -File |
        Measure-Object).Count
    } else { 0 }
  }
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'manifest.json') `
  -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $Repo 'config\source-roots.yaml') `
  -Destination (Join-Path $ConfigDestination 'source-roots.yaml')
foreach ($Name in @('settings.yaml','.knowledgeignore')) {
  $Source = Join-Path $Repo ('config\' + $Name)
  if (Test-Path -LiteralPath $Source) {
    Copy-Item -LiteralPath $Source -Destination (Join-Path $ConfigDestination $Name)
  }
}
$EnvKeys = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match '^[^#][^=]+=' } |
  ForEach-Object { ($_ -split '=',2)[0] }
$EnvKeys | ConvertTo-Json | Set-Content -LiteralPath `
  (Join-Path $ConfigDestination 'environment-keys.json') -Encoding UTF8
$ManagedVault = Join-Path $env:LKP_VAULT_DIR '_generated'
if (Test-Path -LiteralPath $ManagedVault) {
  Copy-Item -LiteralPath $ManagedVault -Destination $VaultDestination -Recurse
}
if (Test-Path -LiteralPath $env:LKP_HOOK_SPOOL_DIR) {
  Copy-Item -LiteralPath $env:LKP_HOOK_SPOOL_DIR `
    -Destination (Join-Path $ManifestDestination 'events') -Recurse
}
$Manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath `
  (Join-Path $ManifestDestination 'runtime-manifest.json') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $Repo 'config\source-roots.yaml') `
  -Destination (Join-Path $ManifestDestination 'source-root-manifest.yaml')
uv run --project $Repo python (Join-Path $Repo 'scripts\record_backup.py') `
  --path $Destination --manifest (Join-Path $Destination 'manifest.json') | Out-Null
Write-Host "Created immutable backup: $Destination"
