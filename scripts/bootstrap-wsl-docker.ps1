[CmdletBinding()]
param(
  [string]$Distribution = 'Ubuntu',
  [string]$DockerRoot = 'C:\Docker\local-knowledge-portal',
  [string]$DataRoot = 'E:\Data\LocalKnowledgePortal',
  [string]$BackupRoot = 'D:\LocalBackup\LocalKnowledgePortal',
  [string]$ModelRoot = 'E:\AI\Models\Ollama'
)
$ErrorActionPreference = 'Stop'
$RepoWindows = Split-Path -Parent $PSScriptRoot
$WslUser = (& wsl.exe -d $Distribution -- bash -lc 'printf %s "$USER"').Trim()
$WslHome = "/home/$WslUser"
$RepoLinux = "$WslHome/src/local-knowledge-portal"
$Exists = (& wsl.exe -d $Distribution -- bash -lc "test -f '$RepoLinux/pyproject.toml'; echo `$?").Trim()
if ($Exists -ne '0') {
  throw "Expected Linux-native checkout at $RepoLinux"
}

$ConfigRoot = Join-Path $DockerRoot 'config'
$HookRoot = Join-Path $DockerRoot 'hooks'
$FallbackRoot = Join-Path $env:LOCALAPPDATA 'LocalKnowledgePortal\spool-fallback'
$Directories = @(
  $DockerRoot,
  $ConfigRoot,
  $HookRoot,
  $FallbackRoot,
  (Join-Path $FallbackRoot 'pending'),
  $DataRoot,
  (Join-Path $DataRoot 'cache'),
  (Join-Path $DataRoot 'ingest\codex-spool\pending'),
  (Join-Path $DataRoot 'ingest\codex-spool\processed'),
  (Join-Path $DataRoot 'ingest\codex-spool\processing'),
  (Join-Path $DataRoot 'ingest\codex-spool\quarantine'),
  (Join-Path $DataRoot 'runtime\logs'),
  (Join-Path $DataRoot 'vault\_generated'),
  (Join-Path $DataRoot 'exports'),
  $ModelRoot,
  $BackupRoot,
  (Join-Path $BackupRoot 'database'),
  (Join-Path $BackupRoot 'config'),
  (Join-Path $BackupRoot 'vault'),
  (Join-Path $BackupRoot 'manifests'),
  (Join-Path $BackupRoot 'restore-tests')
)
New-Item -ItemType Directory -Force -Path $Directories | Out-Null

Copy-Item -LiteralPath (Join-Path $RepoWindows 'infra\docker\compose.wsl.yaml') `
  -Destination (Join-Path $DockerRoot 'compose.yaml') -Force
Copy-Item -LiteralPath (Join-Path $RepoWindows 'scripts\codex-hook-standalone.ps1') `
  -Destination (Join-Path $HookRoot 'codex-hook.ps1') -Force

$SourceConfig = Join-Path $ConfigRoot 'source-roots.yaml'
if (-not (Test-Path -LiteralPath $SourceConfig)) {
  $Template = Get-Content -Raw -LiteralPath (
    Join-Path $RepoWindows 'config\source-roots.wsl.example.yaml'
  )
  $Template = $Template.Replace('YOUR_USER', $WslUser)
  [IO.File]::WriteAllText($SourceConfig, $Template, (New-Object Text.UTF8Encoding($false)))
}
$SettingsConfig = Join-Path $ConfigRoot 'settings.yaml'
if (-not (Test-Path -LiteralPath $SettingsConfig)) {
  Copy-Item -LiteralPath (Join-Path $RepoWindows 'config\settings.wsl.example.yaml') `
    -Destination $SettingsConfig
}

$EnvironmentPath = Join-Path $DockerRoot '.env'
if (-not (Test-Path -LiteralPath $EnvironmentPath)) {
  $Random = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($Random)
  $Password = ([BitConverter]::ToString($Random)).Replace('-', '').ToLowerInvariant()
  $Lines = @(
    'COMPOSE_PROJECT_NAME=local-knowledge-portal',
    'LKP_IMAGE_TAG=wsl',
    "LKP_REPO_PATH=$RepoLinux",
    'LKP_CONFIG_ROOT=/mnt/c/Docker/local-knowledge-portal/config',
    'LKP_DATA_ROOT=/mnt/e/Data/LocalKnowledgePortal',
    'LKP_BACKUP_ROOT=/mnt/d/LocalBackup/LocalKnowledgePortal',
    'LKP_MODEL_ROOT=/mnt/e/AI/Models/Ollama',
    "LKP_SOURCE_ROOT=$WslHome/src",
    "LKP_HOOK_FALLBACK_ROOT=/mnt/c/Users/$env:USERNAME/AppData/Local/LocalKnowledgePortal/spool-fallback",
    'POSTGRES_DB=lkp',
    'POSTGRES_USER=lkp',
    "POSTGRES_PASSWORD=$Password",
    'LKP_CORS_ORIGINS=http://127.0.0.1:3010,http://localhost:3010',
    'LKP_HOOK_COLLECTOR_POLL_SECONDS=2',
    'LKP_HOOK_CLAIM_STALE_SECONDS=60',
    'LKP_RECONCILIATION_SECONDS=300',
    'LKP_FILE_STABILITY_SECONDS=0.5',
    'LKP_EMBEDDING_PROVIDER=ollama',
    'LKP_EMBEDDING_MODEL=qwen3-embedding:0.6b',
    'LKP_EMBEDDING_DIMENSION=1024',
    'LKP_EMBEDDING_REVISION=ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1',
    'LKP_EMBEDDING_MODEL_DIGEST=ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d',
    'LKP_GENERATION_PROVIDER=disabled'
  )
  [IO.File]::WriteAllText(
    $EnvironmentPath,
    ($Lines -join [Environment]::NewLine) + [Environment]::NewLine,
    (New-Object Text.UTF8Encoding($false))
  )
}

$env:LKP_HOOK_SCRIPT = Join-Path $HookRoot 'codex-hook.ps1'
$env:LKP_HOOK_SPOOL_DIR = Join-Path $DataRoot 'ingest\codex-spool'
$env:LKP_HOOK_SPOOL_FALLBACK_DIR = $FallbackRoot
$env:LKP_BACKUP_DIR = $BackupRoot
$env:LKP_RUNTIME_DIR = Join-Path $DataRoot 'runtime'
& (Join-Path $RepoWindows 'scripts\install-codex-hook.ps1')

Write-Host "WSL repository: $RepoLinux"
Write-Host "Compose definition: $(Join-Path $DockerRoot 'compose.yaml')"
Write-Host "Data: $DataRoot"
Write-Host "Backup: $BackupRoot"
