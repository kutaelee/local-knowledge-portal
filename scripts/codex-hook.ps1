[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$PrimarySpool = if ($env:LKP_HOOK_SPOOL_DIR) {
  $env:LKP_HOOK_SPOOL_DIR
} else {
  'E:\LocalKnowledgePortal\ingest\codex-spool'
}
$FallbackSpool = Join-Path $env:LOCALAPPDATA 'LocalKnowledgePortal\spool-fallback'
$HookInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($HookInput)) { exit 0 }

try {
  $HookInput | uv run --project $Repo python -m lkp_indexer.hook_spool `
    --spool-root $PrimarySpool --fallback-root $FallbackSpool | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "raw spool writer exited with code $LASTEXITCODE"
  }
  exit 0
} catch {
  # Last-resort envelope: retain identity and failure state, never unredacted content.
  try {
    $Bytes = [Text.Encoding]::UTF8.GetBytes($HookInput)
    $Hasher = [Security.Cryptography.SHA256]::Create()
    $Hash = ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    $Event = $null
    try { $Event = $HookInput | ConvertFrom-Json } catch {}
    $Envelope = [ordered]@{
      schema_version = 1
      event_id = $Hash
      received_at = (Get-Date).ToUniversalTime().ToString('o')
      status = 'runtime_unavailable'
      payload_hash = $Hash
      payload_bytes = $Bytes.Length
      event_name = if ($Event) { $Event.hook_event_name } else { 'Unknown' }
      session_id = if ($Event) { $Event.session_id } else { '' }
      turn_id = if ($Event) { $Event.turn_id } else { $null }
      payload = @{}
    }
    $Directory = Join-Path $FallbackSpool 'pending'
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $Target = Join-Path $Directory ($Hash + '.json')
    if (-not (Test-Path -LiteralPath $Target)) {
      $Temporary = Join-Path $Directory ('.' + $Hash + '.' + [Guid]::NewGuid() + '.tmp')
      $Json = $Envelope | ConvertTo-Json -Depth 8 -Compress
      [IO.File]::WriteAllText($Temporary, $Json, (New-Object Text.UTF8Encoding($false)))
      Move-Item -LiteralPath $Temporary -Destination $Target
    }
  } catch {}
  exit 0
}
