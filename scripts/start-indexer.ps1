[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Runtime = 'E:\LocalKnowledgePortal\runtime'
$PidDirectory = Join-Path $Runtime 'pid'
$LogDirectory = Join-Path $Runtime 'logs'
New-Item -ItemType Directory -Force -Path $PidDirectory, $LogDirectory | Out-Null

function Start-LkpProcess {
  param([string]$Name, [string]$Module)
  $PidFile = Join-Path $PidDirectory "$Name.pid"
  if (Test-Path -LiteralPath $PidFile) {
    $ExistingPid = [int](Get-Content -Raw -LiteralPath $PidFile)
    if (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue) {
      Write-Host "$Name already running (PID $ExistingPid)."
      return
    }
  }
  $env:LKP_SERVICE_PID_FILE = $PidFile
  Start-Process -FilePath 'uv' -ArgumentList @(
    'run','--project',$Repo,'python','-m',$Module
  ) -WorkingDirectory $Repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDirectory "$Name.stdout.log") `
    -RedirectStandardError (Join-Path $LogDirectory "$Name.stderr.log")
  $Deadline = (Get-Date).AddSeconds(10)
  do { Start-Sleep -Milliseconds 100 } until ((Test-Path -LiteralPath $PidFile) -or (Get-Date) -gt $Deadline)
  if (-not (Test-Path -LiteralPath $PidFile)) { throw "$Name did not publish its PID." }
  Write-Host "$Name started (PID $(Get-Content -Raw -LiteralPath $PidFile))."
}

Start-LkpProcess -Name 'worker' -Module 'lkp_indexer.worker_service'
Start-LkpProcess -Name 'watcher' -Module 'lkp_indexer.watcher_service'
