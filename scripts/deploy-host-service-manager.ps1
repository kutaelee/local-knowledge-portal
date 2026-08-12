[CmdletBinding()]
param(
  [string]$OperationalRoot = 'C:\Docker\local-knowledge-portal',
  [string]$TaskPath = '\Codex\',
  [string]$TaskName = 'Local Knowledge Service Manager',
  [int]$StopTimeoutSeconds = 10,
  [int]$StartTimeoutSeconds = 20
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'host_service_manager.py'
$installed = Join-Path $OperationalRoot 'host-manager\service_manager.py'
$maintenanceMarker = Join-Path $OperationalRoot 'host-manager\maintenance.disabled'
$backupRoot = Join-Path $OperationalRoot 'backups\host-manager'

if (-not (Test-Path -LiteralPath $source)) {
  throw "Missing host service manager source: $source"
}
if (-not (Test-Path -LiteralPath $installed)) {
  throw "Missing installed host service manager: $installed"
}
$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
  throw "Missing scheduled task: $TaskPath$TaskName"
}

New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$backup = Join-Path $backupRoot "service_manager.$stamp.py.bak"
Copy-Item -LiteralPath $installed -Destination $backup
New-Item -ItemType File -Force -Path $maintenanceMarker | Out-Null

$restored = $false
try {
  Disable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
  Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
  $stopDeadline = (Get-Date).AddSeconds($StopTimeoutSeconds)
  do {
    $listener = Get-NetTCPConnection -State Listen -LocalPort 8791 -ErrorAction SilentlyContinue
    if ($listener) {
      $listeners = @($listener)
      if ($listeners.Count -ne 1) {
        throw 'Expected exactly one host service manager listener.'
      }
      $process = Get-CimInstance Win32_Process -Filter (
        "ProcessId=$($listeners[0].OwningProcess)"
      )
      $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
      $expectedOwner = "$env:USERDOMAIN\$env:USERNAME"
      $actualOwner = "$($owner.Domain)\$($owner.User)"
      $expectedScript = $installed.ToLowerInvariant()
      $actualCommand = [string]$process.CommandLine
      if (
        $actualOwner -ne $expectedOwner -or
        -not $actualCommand.ToLowerInvariant().Contains($expectedScript)
      ) {
        throw 'Refusing to stop an unowned or unexpected process on port 8791.'
      }
      Stop-Process -Id $process.ProcessId -Force
      Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 250
  } until (-not $listener -or (Get-Date) -ge $stopDeadline)
  if (Get-NetTCPConnection -State Listen -LocalPort 8791 -ErrorAction SilentlyContinue) {
    throw 'Verified host service manager listener did not stop in time.'
  }

  Copy-Item -LiteralPath $source -Destination $installed -Force
  Enable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
  Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
  $startDeadline = (Get-Date).AddSeconds($StartTimeoutSeconds)
  $health = $null
  do {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8791/api/health' -TimeoutSec 2
    } catch {
      $health = $null
    }
  } until (
    ($health.status -eq 'healthy' -and $health.capabilities -contains 'loopback_web_url') -or
    (Get-Date) -ge $startDeadline
  )
  if (
    $health.status -ne 'healthy' -or
    $health.capabilities -notcontains 'loopback_web_url'
  ) {
    throw 'Updated host service manager did not become healthy.'
  }
} catch {
  Copy-Item -LiteralPath $backup -Destination $installed -Force
  $restored = $true
  Enable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue |
    Out-Null
  Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
  throw
} finally {
  Remove-Item -LiteralPath $maintenanceMarker -Force -ErrorAction SilentlyContinue
}

[pscustomobject]@{
  status = $health.status
  backup = $backup
  restored = $restored
  source_hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
  installed_hash = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
}
