[CmdletBinding()]
param(
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 60,
    [string]$OperationsRoot = "C:\Docker\local-knowledge-portal",
    [string]$TaskPath = "\LocalKnowledgePortal\",
    [string]$TaskName = "CurateKnowledge"
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "curate.ps1"
$target = Join-Path $OperationsRoot "curate.ps1"
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Curation entrypoint is missing: $source"
}
if (-not (Test-Path -LiteralPath $OperationsRoot -PathType Container)) {
    throw "Operational root is missing. Run bootstrap first: $OperationsRoot"
}
if (Test-Path -LiteralPath $target -PathType Leaf) {
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($sourceHash -ne $targetHash) {
        $stamp = Get-Date -Format "yyyy-MM-ddTHHmmssfff"
        Copy-Item -LiteralPath $target -Destination "$target.$stamp.bak" -ErrorAction Stop
    }
}
Copy-Item -LiteralPath $source -Destination $target -Force

$arguments = (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$target`""
)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$startAt = (Get-Date).AddMinutes(5)
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Process the full evidence-gated candidate snapshot through gpuq every $IntervalMinutes minutes." `
    -Force | Out-Null

$registered = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Write-Host "Registered $TaskPath$TaskName; next run $startAt; state $($registered.State)."
