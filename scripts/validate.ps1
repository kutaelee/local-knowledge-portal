[CmdletBinding()]
param([switch]$Integration)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
uv run --project $Repo ruff check services tests
uv run --project $Repo pytest $(if ($Integration) { @() } else { @('-m','not integration') })
pnpm --dir $Repo --filter '@lkp/web' build
