[CmdletBinding()]
param(
  [string]$SchedulerUrl = 'http://127.0.0.1:8790',
  [string]$DockerExecutable = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
  [string]$WslDistribution = 'Ubuntu',
  [string]$RepositoryPath = '/home/kutae/src/local-knowledge-portal',
  [string]$ComposeFile = 'infra/docker/compose.wsl.yaml',
  [string]$SnapshotPath = 'E:\Data\LocalKnowledgePortal\runtime\gpu-embedding-reaper.json',
  [ValidateRange(15, 3600)][int]$PollSeconds = 60,
  [switch]$Once
)

$ErrorActionPreference = 'Stop'
$workload = 'local-knowledge-portal-embedding-reindex'
$containerName = 'local-knowledge-portal-ollama-embedding-batch-1'

function Write-AtomicSnapshot {
  param([Parameter(Mandatory)][hashtable]$Payload)
  $directory = Split-Path -Parent $SnapshotPath
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $temporary = Join-Path $directory ('.gpu-embedding-reaper.{0}.tmp' -f $PID)
  try {
    [IO.File]::WriteAllText(
      $temporary,
      ($Payload | ConvertTo-Json -Depth 5 -Compress),
      (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $SnapshotPath -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force
    }
  }
}

function Invoke-ReapCheck {
  $result = [ordered]@{
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    workload = $workload
    container = $containerName
    state = 'unknown'
    scheduler_ok = $false
    active_job_ids = @()
    action = 'none'
    error = $null
  }
  try {
    if (-not (Test-Path -LiteralPath $DockerExecutable)) {
      throw "Docker CLI not found: $DockerExecutable"
    }
    $health = Invoke-RestMethod -Uri "$SchedulerUrl/api/health" -TimeoutSec 3
    if (-not $health.ok) {
      $result.state = 'scheduler_unhealthy'
      return $result
    }
    $result.scheduler_ok = $true
    $running = @(& $DockerExecutable ps --filter "name=^/$containerName$" --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) {
      throw 'docker ps failed'
    }
    if (-not $running) {
      $result.state = 'no_batch_container'
      return $result
    }
    $status = Invoke-RestMethod -Uri "$SchedulerUrl/api/status" -TimeoutSec 3
    $active = @($status.jobs.active | Where-Object { $_.workload_key -eq $workload })
    $result.active_job_ids = @($active | ForEach-Object { $_.id })
    if ($active.Count -gt 0) {
      $result.state = 'active_batch_retained'
      return $result
    }
    $result.state = 'orphan_batch'
    & wsl.exe -d $WslDistribution -- sh -lc (
      "cd '$RepositoryPath' && docker compose " +
      "--env-file /mnt/c/Docker/local-knowledge-portal/.env " +
      "-f '$ComposeFile' --profile manual-embedding stop ollama-embedding-batch"
    ) | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw 'Compose stop for orphan GPU embedding batch failed'
    }
    $result.action = 'stopped_orphan_batch'
    return $result
  } catch {
    $result.state = 'check_failed'
    $result.error = $_.Exception.Message
    return $result
  }
}

do {
  Write-AtomicSnapshot -Payload (Invoke-ReapCheck)
  if ($Once) { break }
  Start-Sleep -Seconds $PollSeconds
} while ($true)
