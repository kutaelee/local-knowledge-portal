[CmdletBinding()]
param(
  [string]$OperationalRoot = 'C:\Docker\local-knowledge-portal',
  [string]$ManagerTaskName = '\Codex\Local Knowledge Service Manager',
  [string]$GuardTaskName = '\Codex\Local Knowledge Service Manager Guard',
  [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'
if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 60) {
  throw 'IntervalMinutes must be between 1 and 60.'
}

function Split-TaskIdentity([string]$QualifiedName) {
  $path = Split-Path -Parent $QualifiedName
  $leaf = Split-Path -Leaf $QualifiedName
  if (-not $path.EndsWith('\')) { $path += '\' }
  return @{ Path = $path; Name = $leaf }
}

$sourceGuard = Join-Path $PSScriptRoot 'ensure-service-manager.ps1'
if (-not (Test-Path -LiteralPath $sourceGuard)) {
  throw "Missing service manager guard source: $sourceGuard"
}

$managerRoot = Join-Path $OperationalRoot 'host-manager'
$installedGuard = Join-Path $managerRoot 'ensure-service-manager.ps1'
$maintenanceMarker = Join-Path $managerRoot 'maintenance.disabled'
$backupRoot = Join-Path $OperationalRoot 'backups\host-manager'
New-Item -ItemType Directory -Force -Path $managerRoot | Out-Null

if (Test-Path -LiteralPath $installedGuard) {
  $sourceHash = (Get-FileHash -LiteralPath $sourceGuard -Algorithm SHA256).Hash
  $installedHash = (Get-FileHash -LiteralPath $installedGuard -Algorithm SHA256).Hash
  if ($sourceHash -ne $installedHash) {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    Copy-Item -LiteralPath $installedGuard -Destination (
      Join-Path $backupRoot "ensure-service-manager.$stamp.ps1.bak"
    )
  }
}
Copy-Item -LiteralPath $sourceGuard -Destination $installedGuard -Force

$managerIdentity = Split-TaskIdentity $ManagerTaskName
$guardIdentity = Split-TaskIdentity $GuardTaskName
$managerTask = Get-ScheduledTask `
  -TaskPath $managerIdentity.Path `
  -TaskName $managerIdentity.Name `
  -ErrorAction SilentlyContinue
if ($null -eq $managerTask) {
  throw "Service manager task is not installed: $ManagerTaskName"
}

$guardArguments = @(
  '-NoProfile',
  '-NonInteractive',
  '-WindowStyle', 'Hidden',
  '-ExecutionPolicy', 'Bypass',
  '-File', "`"$installedGuard`"",
  # A quoted Windows argv value ending in a backslash escapes its closing quote.
  # Task Scheduler would merge the next flag into TaskPath and the guard exits 1.
  '-TaskPath', $managerIdentity.Path,
  '-TaskName', "`"$($managerIdentity.Name)`"",
  '-MaintenanceMarker', "`"$maintenanceMarker`""
) -join ' '
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $guardArguments
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$periodicTrigger = New-ScheduledTaskTrigger `
  -Once `
  -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited
Register-ScheduledTask `
  -TaskName $guardIdentity.Name `
  -TaskPath $guardIdentity.Path `
  -Action $action `
  -Trigger @($logonTrigger, $periodicTrigger) `
  -Settings $settings `
  -Principal $principal `
  -Description 'Keeps the CPU-only Local Knowledge control plane available independently of GPU and LLM schedules.' `
  -Force | Out-Null

$repair = & $installedGuard `
  -TaskPath $managerIdentity.Path `
  -TaskName $managerIdentity.Name `
  -MaintenanceMarker $maintenanceMarker

[pscustomobject]@{
  status = $repair.status
  repaired = $repair.repaired
  manager_task = $ManagerTaskName
  guard_task = $GuardTaskName
  interval_minutes = $IntervalMinutes
  maintenance_marker = $maintenanceMarker
}
