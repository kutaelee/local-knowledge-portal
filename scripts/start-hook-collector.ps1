[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$RuntimeDir = if ($env:LKP_RUNTIME_DIR) { $env:LKP_RUNTIME_DIR } else { 'E:\LocalKnowledgePortal\runtime' }
$PidDir = Join-Path $RuntimeDir 'pid'
$LogDir = Join-Path $RuntimeDir 'logs'
$PidFile = Join-Path $PidDir 'hook-collector.pid'
New-Item -ItemType Directory -Force -Path $PidDir, $LogDir | Out-Null
if (Test-Path -LiteralPath $PidFile) {
  $ExistingPid = [int](Get-Content -LiteralPath $PidFile -Raw)
  if (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue) {
    Write-Host "Hook collector is already running (PID $ExistingPid)."
    exit 0
  }
}
$env:LKP_SERVICE_PID_FILE = $PidFile
Start-Process -FilePath 'uv' -ArgumentList @(
  'run', '--project', $Repo, 'python', '-m', 'lkp_indexer.hook_collector', '--watch'
) -WorkingDirectory $Repo -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogDir 'hook-collector.stdout.log') `
  -RedirectStandardError (Join-Path $LogDir 'hook-collector.stderr.log')
$Deadline = (Get-Date).AddSeconds(10)
do { Start-Sleep -Milliseconds 100 } until ((Test-Path -LiteralPath $PidFile) -or (Get-Date) -gt $Deadline)
if (-not (Test-Path -LiteralPath $PidFile)) { throw 'Hook collector did not publish its PID.' }
Write-Host "Hook collector started (PID $(Get-Content -Raw -LiteralPath $PidFile))."
