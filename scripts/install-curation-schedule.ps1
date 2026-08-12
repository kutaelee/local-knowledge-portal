[CmdletBinding()]
param(
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 60,
    [string]$OperationsRoot = "C:\Docker\local-knowledge-portal",
    [string]$TaskPath = "\LocalKnowledgePortal\",
    [string]$TaskName = "CurateKnowledge"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $OperationsRoot -PathType Container)) {
    throw "Operational root is missing. Run bootstrap first: $OperationsRoot"
}

foreach ($fileName in @("curate.ps1", "curation-queue.psm1")) {
    $source = Join-Path $PSScriptRoot $fileName
    $target = Join-Path $OperationsRoot $fileName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Curation runtime file is missing: $source"
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
}

# `$target` is the loop's final value (the .psm1 module).  Keep the scheduled
# task pointed at the executable entrypoint explicitly; otherwise Task
# Scheduler reports a successful launch while no curation workload is submitted.
$curateTarget = Join-Path $OperationsRoot "curate.ps1"
$arguments = (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$curateTarget`""
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
