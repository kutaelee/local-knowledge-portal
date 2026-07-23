[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$HooksPath = Join-Path $CodexHome 'hooks.json'
if (Test-Path -LiteralPath $HooksPath) {
  $Existing = Get-Content -LiteralPath $HooksPath -Raw | ConvertFrom-Json
  if ($Existing.description -ne 'Send completed Codex turns to Local Knowledge Portal managed wiki pages.') {
    throw "Existing hooks.json was not changed. Merge the Stop hook manually: $HooksPath"
  }
}
$HookScript = Join-Path $PSScriptRoot 'codex-hook.ps1'
$Command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$HookScript`""
$Payload = @{
  description = 'Send completed Codex turns to Local Knowledge Portal managed wiki pages.'
  hooks = @{
    Stop = @(
      @{
        hooks = @(
          @{
            type = 'command'
            commandWindows = $Command
            command = $Command
            timeout = 30
            statusMessage = 'Saving Codex session to the local knowledge portal'
          }
        )
      }
    )
  }
}
$Json = $Payload | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($HooksPath, $Json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Installed $HooksPath. Open /hooks in a new Codex session and trust this exact hook."
