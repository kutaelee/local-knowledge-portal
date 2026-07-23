[CmdletBinding()]
param(
  [string]$ComposeFile = 'C:\Docker\local-knowledge-portal\compose.yaml',
  [string]$DockerExecutable = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
  [string]$DockerDesktopExecutable = 'C:\Program Files\Docker\Docker\Docker Desktop.exe',
  [string]$WslExecutable = 'C:\Windows\System32\wsl.exe',
  [string]$WslDistribution = 'Ubuntu',
  [string]$ComposeDirectory = '/mnt/c/Docker/local-knowledge-portal',
  [string]$ComposeName = 'compose.yaml',
  [string]$HealthUrl = 'http://127.0.0.1:8010/health/ready',
  [string]$LogPath = 'E:\Data\LocalKnowledgePortal\runtime\logs\startup.jsonl',
  [int]$DockerTimeoutSeconds = 180,
  [int]$HealthTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Write-StartupEvent {
  param(
    [Parameter(Mandatory)][string]$Event,
    [string]$Level = 'info',
    [string]$Detail = ''
  )
  $parent = Split-Path -Parent $LogPath
  if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $record = [ordered]@{
    timestamp = [DateTimeOffset]::Now.ToString('o')
    level = $Level
    service = 'local-knowledge-portal-startup'
    event = $Event
    detail = $Detail
  }
  Add-Content -LiteralPath $LogPath -Value ($record | ConvertTo-Json -Compress)
}

function Test-DockerReady {
  & $DockerExecutable info *> $null
  return $LASTEXITCODE -eq 0
}

try {
  if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "Compose file not found: $ComposeFile"
  }
  if (-not (Test-Path -LiteralPath $DockerExecutable)) {
    throw "Docker CLI not found: $DockerExecutable"
  }

  Write-StartupEvent -Event 'startup_begin'
  if (-not (Test-DockerReady)) {
    if (-not (Test-Path -LiteralPath $DockerDesktopExecutable)) {
      throw "Docker Desktop not found: $DockerDesktopExecutable"
    }
    Start-Process -FilePath $DockerDesktopExecutable -WindowStyle Hidden
    Write-StartupEvent -Event 'docker_desktop_start_requested'
    $deadline = [DateTimeOffset]::Now.AddSeconds($DockerTimeoutSeconds)
    while ([DateTimeOffset]::Now -lt $deadline) {
      Start-Sleep -Seconds 2
      if (Test-DockerReady) { break }
    }
    if (-not (Test-DockerReady)) {
      throw "Docker Desktop did not become ready within $DockerTimeoutSeconds seconds"
    }
  }
  Write-StartupEvent -Event 'docker_ready'

  if (-not (Test-Path -LiteralPath $WslExecutable)) {
    throw "WSL executable not found: $WslExecutable"
  }
  & $WslExecutable `
    -d $WslDistribution `
    --cd $ComposeDirectory `
    docker compose -f $ComposeName up -d
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed with exit code $LASTEXITCODE"
  }
  Write-StartupEvent -Event 'compose_up_complete'

  $healthDeadline = [DateTimeOffset]::Now.AddSeconds($HealthTimeoutSeconds)
  $ready = $false
  while ([DateTimeOffset]::Now -lt $healthDeadline) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 5
      if ($response.StatusCode -eq 200) {
        $ready = $true
        break
      }
    } catch {
      Start-Sleep -Seconds 2
    }
  }
  if (-not $ready) {
    throw "Portal readiness did not become healthy within $HealthTimeoutSeconds seconds"
  }
  Write-StartupEvent -Event 'portal_ready'
  exit 0
} catch {
  Write-StartupEvent -Event 'startup_failed' -Level 'error' -Detail $_.Exception.Message
  exit 1
}
