[CmdletBinding()]
param(
  [string]$OperationsRoot = 'C:\Docker\local-knowledge-portal',
  [string]$TaskPath = '\LocalKnowledgePortal\',
  [string]$TaskName = 'StartAtLogon'
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'start-at-login.ps1'
$target = Join-Path $OperationsRoot 'start-at-login.ps1'
if (-not (Test-Path -LiteralPath $OperationsRoot)) {
  New-Item -ItemType Directory -Path $OperationsRoot -Force | Out-Null
}
Copy-Item -LiteralPath $source -Destination $target -Force

$arguments = (
  '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass ' +
  "-File `"$target`""
)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT20S'
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
  -TaskPath $TaskPath `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description 'Start and verify the Local Knowledge Portal after Windows logon.' `
  -Force | Out-Null

Write-Host "Registered $TaskPath$TaskName using $target"
