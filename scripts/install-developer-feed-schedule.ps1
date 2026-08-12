[CmdletBinding()]
param(
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 180,
    [ValidateRange(0, 23)]
    [int]$DailyHour = 18,
    [string]$OperationsRoot = "C:\Docker\local-knowledge-portal",
    [string]$TaskPath = "\LocalKnowledgePortal\",
    [string]$TaskName = "PublishInformationFeed"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $OperationsRoot -PathType Container)) {
    throw "Operational root is missing. Run bootstrap first: $OperationsRoot"
}

foreach ($fileName in @("publish-developer-feed.ps1", "curation-queue.psm1")) {
    $source = Join-Path $PSScriptRoot $fileName
    $target = Join-Path $OperationsRoot $fileName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Developer feed runtime file is missing: $source"
    }
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            $stamp = Get-Date -Format "yyyy-MM-ddTHHmmssfff"
            Copy-Item -LiteralPath $target -Destination "$target.$stamp.bak"
        }
    }
    Copy-Item -LiteralPath $source -Destination $target -Force
}

$entrypoint = Join-Path $OperationsRoot "publish-developer-feed.ps1"
$arguments = (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$entrypoint`""
)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date.AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$dailyTrigger = New-ScheduledTaskTrigger `
    -Daily `
    -At (Get-Date).Date.AddHours($DailyHour)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($repeatTrigger, $dailyTrigger) `
    -Principal $principal `
    -Settings $settings `
    -Description "Write content-based updates every $IntervalMinutes minutes and a daily summary at $DailyHour`:00 through gpuq using Gemma 4 12B." `
    -Force | Out-Null

$registered = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Write-Host "Registered $TaskPath$TaskName; state $($registered.State)."
