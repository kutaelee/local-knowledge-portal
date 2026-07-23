[CmdletBinding()]
param([switch]$Index)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$RuntimeDir = if ($env:LKP_RUNTIME_DIR) { $env:LKP_RUNTIME_DIR } else { 'E:\LocalKnowledgePortal\runtime' }
$PidDir = Join-Path $RuntimeDir 'pid'
$LogDir = Join-Path $RuntimeDir 'logs'
$PidFile = Join-Path $PidDir 'codex-capture.pid'
New-Item -ItemType Directory -Force -Path $PidDir, $LogDir | Out-Null

if (Test-Path -LiteralPath $PidFile) {
  $ExistingPid = [int](Get-Content -LiteralPath $PidFile -Raw)
  if (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue) {
    Write-Host "Codex capture is already running (PID $ExistingPid)."
    exit 0
  }
}

$Arguments = @(
  'run', '--project', $Repo, 'python', '-m', 'lkp_indexer.codex_capture',
  '--watch', '--codex-home', (Join-Path $env:USERPROFILE '.codex')
)
if ($Index) { $Arguments += '--index' }
$Process = Start-Process -FilePath 'uv' -ArgumentList $Arguments -WorkingDirectory $Repo `
  -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $LogDir 'codex-capture.stdout.log') `
  -RedirectStandardError (Join-Path $LogDir 'codex-capture.stderr.log')
Set-Content -LiteralPath $PidFile -Value $Process.Id -Encoding ascii
Write-Host "Codex capture started (PID $($Process.Id))."
