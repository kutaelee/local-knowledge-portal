[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'start.ps1')
Start-Process -FilePath 'uv' -ArgumentList @(
  'run','uvicorn','lkp.main:app','--reload','--host','127.0.0.1','--port','8010'
) -WorkingDirectory $Repo -WindowStyle Hidden
pnpm --dir $Repo --filter '@lkp/web' dev
