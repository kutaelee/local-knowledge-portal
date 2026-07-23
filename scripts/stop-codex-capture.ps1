[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RuntimeDir = if ($env:LKP_RUNTIME_DIR) { $env:LKP_RUNTIME_DIR } else { 'E:\LocalKnowledgePortal\runtime' }
$PidFile = Join-Path (Join-Path $RuntimeDir 'pid') 'codex-capture.pid'
if (-not (Test-Path -LiteralPath $PidFile)) {
  Write-Host 'Codex capture is not running.'
  exit 0
}
$CapturePid = [int](Get-Content -LiteralPath $PidFile -Raw)
$Process = Get-Process -Id $CapturePid -ErrorAction SilentlyContinue
if ($Process) {
  Stop-Process -Id $CapturePid
  $Process.WaitForExit(10000)
}
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Codex capture stopped (PID $CapturePid)."
