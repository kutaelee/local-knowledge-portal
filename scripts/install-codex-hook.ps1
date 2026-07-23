[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$HooksPath = Join-Path $CodexHome 'hooks.json'
$HookScript = Join-Path $PSScriptRoot 'codex-hook.ps1'
$Command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$HookScript`""
$Description = 'Local Knowledge Portal raw-spool hooks (managed entry, merge-safe).'
$Events = [ordered]@{
  SessionStart = 'startup|resume|clear|compact'
  UserPromptSubmit = $null
  PostToolUse = '*'
  Stop = $null
  SubagentStart = '*'
  SubagentStop = '*'
}

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
$OldJson = if (Test-Path -LiteralPath $HooksPath) {
  Get-Content -LiteralPath $HooksPath -Raw
} else {
  ''
}
$Config = if ($OldJson) {
  $OldJson | ConvertFrom-Json
} else {
  [pscustomobject]@{ description = $Description; hooks = [pscustomobject]@{} }
}
if (-not $Config.PSObject.Properties['hooks']) {
  $Config | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{})
}
if (-not $Config.PSObject.Properties['description']) {
  $Config | Add-Member -NotePropertyName description -NotePropertyValue $Description
}

foreach ($EventName in $Events.Keys) {
  $Group = [ordered]@{
    hooks = @(
      [ordered]@{
        type = 'command'
        commandWindows = $Command
        command = $Command
        timeout = 10
        statusMessage = 'Spooling Codex activity locally'
      }
    )
  }
  if ($Events[$EventName]) { $Group.matcher = $Events[$EventName] }
  $Property = $Config.hooks.PSObject.Properties[$EventName]
  if (-not $Property) {
    $Config.hooks | Add-Member -NotePropertyName $EventName -NotePropertyValue @($Group)
    continue
  }
  $AlreadyInstalled = @($Property.Value) | Where-Object {
    @($_.hooks) | Where-Object { $_.commandWindows -eq $Command }
  }
  if (-not $AlreadyInstalled) {
    $Property.Value = @($Property.Value) + @($Group)
  }
}

$NewJson = $Config | ConvertTo-Json -Depth 20
if ($OldJson -ne $NewJson) {
  $BackupRoot = if ($env:LKP_BACKUP_DIR) {
    Join-Path $env:LKP_BACKUP_DIR 'config\codex'
  } else {
    'D:\Backups\LocalKnowledgePortal\config\codex'
  }
  $Stamp = Get-Date -Format 'yyyy-MM-ddTHHmmssfff'
  $Backup = Join-Path $BackupRoot $Stamp
  New-Item -ItemType Directory -Path $Backup | Out-Null
  foreach ($Name in @('hooks.json', 'config.toml', 'AGENTS.md')) {
    $Source = Join-Path $CodexHome $Name
    if (Test-Path -LiteralPath $Source) {
      Copy-Item -LiteralPath $Source -Destination (Join-Path $Backup $Name)
    }
  }
  $Temporary = Join-Path $CodexHome ('.hooks.' + [Guid]::NewGuid() + '.tmp')
  [IO.File]::WriteAllText($Temporary, $NewJson, (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $Temporary -Destination $HooksPath -Force
}

$RuntimeDir = if ($env:LKP_RUNTIME_DIR) {
  $env:LKP_RUNTIME_DIR
} else {
  'E:\LocalKnowledgePortal\runtime'
}
$StatusPath = Join-Path $RuntimeDir 'hook-trust-status.json'
$Status = [ordered]@{
  status = 'MANUAL_APPROVAL_REQUIRED'
  checked_at = (Get-Date).ToUniversalTime().ToString('o')
  hooks_path = $HooksPath
  events = @($Events.Keys)
  instruction = 'Start a new Codex session, run /hooks, inspect and trust the Local Knowledge Portal entries.'
}
$StatusJson = $Status | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($StatusPath, $StatusJson, (New-Object Text.UTF8Encoding($false)))
Write-Host "Merged raw-spool hooks into $HooksPath"
Write-Host "Trust status: MANUAL_APPROVAL_REQUIRED (run /hooks in a new Codex session)."
