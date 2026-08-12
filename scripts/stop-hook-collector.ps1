[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RuntimeDir = if ($env:LKP_RUNTIME_DIR) { $env:LKP_RUNTIME_DIR } else { 'E:\LocalKnowledgePortal\runtime' }
$PidFile = Join-Path (Join-Path $RuntimeDir 'pid') 'hook-collector.pid'
if (-not (Test-Path -LiteralPath $PidFile)) { exit 0 }
$CollectorPid = [int](Get-Content -LiteralPath $PidFile -Raw)
$Process = Get-Process -Id $CollectorPid -ErrorAction SilentlyContinue
if ($Process) {
  Stop-Process -Id $CollectorPid
  $Process.WaitForExit(10000)
}
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Hook collector stopped (PID $CollectorPid)."
