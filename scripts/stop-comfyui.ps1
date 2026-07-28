[CmdletBinding()]
param([ValidateRange(1, 65535)][int]$Port = 8188)

$ErrorActionPreference = 'Stop'
$expectedMain = 'E:\AI\Apps\ComfyUI\main.py'
$queueUri = "http://127.0.0.1:$Port/queue"

try {
  $queue = Invoke-RestMethod -Uri $queueUri -TimeoutSec 3
} catch {
  throw 'ComfyUI queue state is unavailable; refusing to stop.'
}
if (@($queue.queue_running).Count -gt 0 -or @($queue.queue_pending).Count -gt 0) {
  throw 'ComfyUI has running or queued work; refusing to stop.'
}

$listeners = @(
  Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $Port `
    -ErrorAction SilentlyContinue
)
if ($listeners.Count -eq 0) {
  [pscustomobject]@{ status = 'already-stopped'; port = $Port }
  exit 0
}

$targetIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($targetId in $targetIds) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $targetId"
  if ($null -eq $process) {
    throw "Listener process $targetId disappeared before validation."
  }
  $commandLine = [string]$process.CommandLine
  if (-not $commandLine.Contains($expectedMain) -or
      $commandLine -notmatch "(?:--port\s+|--port=)$Port(?:\s|$)") {
    throw "Port $Port is owned by an unregistered process; refusing to stop."
  }
  $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
  if ($owner.ReturnValue -ne 0 -or $owner.User -ne $env:USERNAME) {
    throw "Listener process $targetId is not owned by the current user."
  }
  Stop-Process -Id $targetId
}

$deadline = (Get-Date).AddSeconds(15)
do {
  Start-Sleep -Milliseconds 250
  $remaining = @(
    Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $Port `
      -ErrorAction SilentlyContinue
  )
} until ($remaining.Count -eq 0 -or (Get-Date) -ge $deadline)

if ($remaining.Count -gt 0) {
  throw "ComfyUI did not release port $Port within 15 seconds."
}
[pscustomobject]@{
  status = 'stopped'
  port = $Port
  process_ids = $targetIds
}
