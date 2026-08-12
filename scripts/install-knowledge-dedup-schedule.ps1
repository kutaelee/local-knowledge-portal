[CmdletBinding()]
param(
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 30
)

$ErrorActionPreference = "Stop"
$TaskPath = "\LocalKnowledgePortal\"
$TaskName = "DeduplicateKnowledge"
$ScriptPath = Join-Path $PSScriptRoot "deduplicate-knowledge.ps1"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Knowledge dedup submission script was not found: $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(7) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description (
    "Low-priority gpuq submission for pending Local Knowledge Portal knowledge duplicate checks."
) -Force | Out-Null
Write-Host "Installed $TaskPath$TaskName every $IntervalMinutes minutes (only submits when candidates are pending)."
