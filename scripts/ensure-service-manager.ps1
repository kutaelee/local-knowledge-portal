[CmdletBinding()]
param(
  [string]$TaskPath = '\Codex\',
  [string]$TaskName = 'Local Knowledge Service Manager',
  [string]$MaintenanceMarker = 'C:\Docker\local-knowledge-portal\host-manager\maintenance.disabled',
  [string]$HealthUrl = 'http://127.0.0.1:8791/api/health',
  [int]$HealthTimeoutSeconds = 2,
  [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = 'Stop'

function Get-ManagerHealth {
  try {
    $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec $HealthTimeoutSeconds
    return $response.status -eq 'healthy'
  } catch {
    return $false
  }
}

if (Test-Path -LiteralPath $MaintenanceMarker) {
  [pscustomobject]@{
    status = 'maintenance-disabled'
    repaired = $false
    task = "$TaskPath$TaskName"
  }
  exit 0
}

$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
  throw "Required control-plane task is not installed: $TaskPath$TaskName"
}

$repaired = $false
if (-not $task.Settings.Enabled) {
  Enable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
  $repaired = $true
  $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
}

if (-not (Get-ManagerHealth)) {
  if ($task.State -eq 'Running') {
    throw 'Service manager task is running but its loopback health check failed.'
  }
  Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
  $repaired = $true

  $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
  do {
    Start-Sleep -Milliseconds 500
  } until ((Get-ManagerHealth) -or (Get-Date) -ge $deadline)
}

if (-not (Get-ManagerHealth)) {
  throw "Service manager did not become healthy within ${StartupTimeoutSeconds}s."
}

[pscustomobject]@{
  status = 'healthy'
  repaired = $repaired
  task = "$TaskPath$TaskName"
}
