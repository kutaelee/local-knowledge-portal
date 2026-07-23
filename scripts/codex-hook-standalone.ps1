[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$MaxPayloadBytes = 1MB
$SupportedEvents = @(
  'SessionStart',
  'UserPromptSubmit',
  'PostToolUse',
  'Stop',
  'SubagentStart',
  'SubagentStop'
)
$PrimarySpool = if ($env:LKP_HOOK_SPOOL_DIR) {
  $env:LKP_HOOK_SPOOL_DIR
} else {
  'E:\LocalKnowledgePortal\ingest\codex-spool'
}
$FallbackSpool = if ($env:LKP_HOOK_SPOOL_FALLBACK_DIR) {
  $env:LKP_HOOK_SPOOL_FALLBACK_DIR
} else {
  Join-Path $env:LOCALAPPDATA 'LocalKnowledgePortal\spool-fallback'
}

function Get-Sha256 {
  param([byte[]]$Bytes)
  $Hasher = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
  } finally {
    $Hasher.Dispose()
  }
}

function Protect-String {
  param([AllowNull()][string]$Value)
  if ($null -eq $Value) { return $null }
  $Result = $Value
  $Patterns = @(
    '(?i)(authorization\s*:\s*bearer\s+)[^\s"'']+',
    '(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+',
    '\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_-]{12,}\b'
  )
  foreach ($Pattern in $Patterns) {
    $Result = [regex]::Replace($Result, $Pattern, '$1[REDACTED]')
  }
  return $Result
}

function Protect-Value {
  param($Value)
  if ($null -eq $Value) { return $null }
  if ($Value -is [string]) { return Protect-String $Value }
  if ($Value -is [Collections.IDictionary]) {
    $Output = [ordered]@{}
    foreach ($Key in $Value.Keys) {
      $Output[$Key] = Protect-Value $Value[$Key]
    }
    return $Output
  }
  if ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
    return @($Value | ForEach-Object { Protect-Value $_ })
  }
  if ($Value -is [psobject]) {
    $Output = [ordered]@{}
    foreach ($Property in $Value.PSObject.Properties) {
      $Output[$Property.Name] = Protect-Value $Property.Value
    }
    return $Output
  }
  return $Value
}

function Write-AtomicEnvelope {
  param([string]$Root, [string]$EventId, [string]$Json)
  $Directory = Join-Path $Root 'pending'
  New-Item -ItemType Directory -Force -Path $Directory | Out-Null
  $Target = Join-Path $Directory ($EventId + '.json')
  if (Test-Path -LiteralPath $Target) { return }
  $Temporary = Join-Path $Directory ('.' + $EventId + '.' + [Guid]::NewGuid() + '.tmp')
  [IO.File]::WriteAllText($Temporary, $Json, (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $Temporary -Destination $Target
}

$RawInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($RawInput)) { exit 0 }
$RawBytes = [Text.Encoding]::UTF8.GetBytes($RawInput)
$PayloadHash = Get-Sha256 $RawBytes
$Status = 'accepted'
$Payload = $null
try {
  $Payload = $RawInput | ConvertFrom-Json
} catch {
  $Status = 'malformed'
}

$EventName = if ($Payload -and $Payload.hook_event_name) {
  [string]$Payload.hook_event_name
} else {
  'Unknown'
}
if ($Status -eq 'accepted' -and $SupportedEvents -notcontains $EventName) {
  $Status = 'unsupported'
}
if ($RawBytes.Length -gt $MaxPayloadBytes) {
  $Status = 'oversized'
  $Payload = [ordered]@{
    hook_event_name = $EventName
    truncated = $true
    original_bytes = $RawBytes.Length
  }
}

$Identity = '{0}|{1}|{2}|{3}' -f $EventName, $Payload.session_id, $Payload.turn_id, $PayloadHash
$EventId = Get-Sha256 ([Text.Encoding]::UTF8.GetBytes($Identity))
$Envelope = [ordered]@{
  schema_version = 1
  event_id = $EventId
  received_at = (Get-Date).ToUniversalTime().ToString('o')
  status = $Status
  payload_hash = $PayloadHash
  payload_bytes = $RawBytes.Length
  event_name = $EventName
  session_id = if ($Payload) { [string]$Payload.session_id } else { '' }
  turn_id = if ($Payload) { $Payload.turn_id } else { $null }
  payload = if ($Payload) { Protect-Value $Payload } else { @{} }
}
$Json = $Envelope | ConvertTo-Json -Depth 30 -Compress

try {
  Write-AtomicEnvelope $PrimarySpool $EventId $Json
} catch {
  try {
    $Envelope.status = 'primary_spool_unavailable'
    $Json = $Envelope | ConvertTo-Json -Depth 30 -Compress
    Write-AtomicEnvelope $FallbackSpool $EventId $Json
  } catch {}
}
exit 0
