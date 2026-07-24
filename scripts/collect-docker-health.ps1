[CmdletBinding()]
param(
  [string]$SnapshotPath = 'E:\Data\LocalKnowledgePortal\runtime\docker-services.json',
  [string]$DockerExecutable = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
  [ValidateRange(15, 3600)][int]$PollSeconds = 30,
  [switch]$Once
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Get-LabelValue {
  param(
    [AllowEmptyString()][string]$Labels,
    [Parameter(Mandatory)][string]$Key
  )
  if (-not $Labels) { return '' }
  $match = [regex]::Match(
    $Labels,
    "(?:^|,)$([regex]::Escape($Key))=([^,]*)"
  )
  if ($match.Success) { return $match.Groups[1].Value }
  return ''
}

function Write-AtomicSnapshot {
  param([Parameter(Mandatory)][hashtable]$Payload)
  $directory = Split-Path -Parent $SnapshotPath
  if (-not (Test-Path -LiteralPath $directory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
  }
  $temporary = Join-Path $directory (
    '.docker-services.{0}.{1}.tmp' -f $PID, [guid]::NewGuid().ToString('N')
  )
  try {
    $json = $Payload | ConvertTo-Json -Depth 6 -Compress
    [IO.File]::WriteAllText(
      $temporary,
      $json,
      (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $SnapshotPath -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force
    }
  }
}

function Collect-DockerSnapshot {
  $payload = @{
    schema_version = 1
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    collector = 'local-knowledge-portal-docker-inventory'
    containers = @()
    error = $null
  }
  try {
    if (-not (Test-Path -LiteralPath $DockerExecutable)) {
      throw "Docker CLI not found: $DockerExecutable"
    }
    $lines = @(& $DockerExecutable ps --format '{{json .}}' 2>&1)
    if ($LASTEXITCODE -ne 0) {
      throw "docker ps failed: $($lines -join ' ')"
    }
    $containers = foreach ($line in $lines) {
      if (-not $line) { continue }
      $row = $line | ConvertFrom-Json
      $project = Get-LabelValue -Labels $row.Labels -Key 'com.docker.compose.project'
      $service = Get-LabelValue -Labels $row.Labels -Key 'com.docker.compose.service'
      $oneoff = Get-LabelValue -Labels $row.Labels -Key 'com.docker.compose.oneoff'
      if ($oneoff -eq 'True') { continue }
      [ordered]@{
        id = $row.ID
        name = $row.Names
        image = $row.Image
        state = $row.State
        status = $row.Status
        health = $row.HealthStatus
        ports = $row.Ports
        project = $project
        service = $service
        working_dir = Get-LabelValue -Labels $row.Labels -Key 'com.docker.compose.project.working_dir'
      }
    }
    $payload.containers = @($containers)
  } catch {
    $payload.error = $_.Exception.Message
  }
  Write-AtomicSnapshot -Payload $payload
}

do {
  Collect-DockerSnapshot
  if ($Once) { break }
  Start-Sleep -Seconds $PollSeconds
} while ($true)
