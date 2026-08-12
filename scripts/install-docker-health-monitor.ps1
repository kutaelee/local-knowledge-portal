[CmdletBinding()]
param(
  [string]$InstallDirectory = 'C:\Docker\local-knowledge-portal',
  [string]$TaskPath = '\LocalKnowledgePortal\',
  [string]$TaskName = 'CollectDockerHealth',
  [ValidateRange(15, 3600)][int]$PollSeconds = 30
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'collect-docker-health.ps1'
$target = Join-Path $InstallDirectory 'collect-docker-health.ps1'
if (-not (Test-Path -LiteralPath $source)) {
  throw "Collector source not found: $source"
}
if (-not (Test-Path -LiteralPath $InstallDirectory)) {
  New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
}
Copy-Item -LiteralPath $source -Destination $target -Force

$powershell = Join-Path $PSHOME 'powershell.exe'
$arguments = (
  '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass ' +
  "-File `"$target`" -PollSeconds $PollSeconds"
)
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal `
  -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskPath $TaskPath `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'Writes a bounded read-only Docker service inventory for Local Knowledge Portal.' `
  -Force | Out-Null
Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName

[pscustomobject]@{
  task = "$TaskPath$TaskName"
  script = $target
  snapshot = 'E:\Data\LocalKnowledgePortal\runtime\docker-services.json'
  interval_seconds = $PollSeconds
}
