[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
try {
  $HookInput = [Console]::In.ReadToEnd()
  if ([string]::IsNullOrWhiteSpace($HookInput)) { exit 0 }
  $Event = $HookInput | ConvertFrom-Json
  if ([string]::IsNullOrWhiteSpace($Event.transcript_path)) { exit 0 }
  & uv run --project $Repo python -m lkp_indexer.codex_capture `
    --transcript ([string]$Event.transcript_path) --index | Out-Null
} catch {
  # The Python command records capture failures. A logging hook must not block Codex.
  exit 0
}
exit 0
