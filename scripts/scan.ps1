[CmdletBinding()]
param([switch]$WorkOnce)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
uv run --project $Repo python -m lkp_indexer.cli scan
if ($WorkOnce) { uv run --project $Repo python -m lkp_indexer.cli work-once }
