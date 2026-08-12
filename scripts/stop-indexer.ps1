[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$PidDirectory = 'E:\LocalKnowledgePortal\runtime\pid'
foreach ($Name in @('watcher', 'worker')) {
  $PidFile = Join-Path $PidDirectory "$Name.pid"
  if (-not (Test-Path -LiteralPath $PidFile)) { continue }
  $ProcessId = [int](Get-Content -Raw -LiteralPath $PidFile)
  $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($Process) {
    Stop-Process -Id $ProcessId
    $Process.WaitForExit(10000) | Out-Null
  }
  Remove-Item -LiteralPath $PidFile -Force
  Write-Host "$Name stopped."
}
