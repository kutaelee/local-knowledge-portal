[CmdletBinding()]
param(
    [ValidateRange(0, 23)]
    [int]$Hour = 7,
    [ValidateRange(0, 59)]
    [int]$Minute = 30,
    [string]$OperationsRoot = "C:\Docker\local-knowledge-portal",
    [string]$TaskPath = "\LocalKnowledgePortal\",
    [string]$TaskName = "NightlyKnowledgeMaintenance"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $OperationsRoot -PathType Container)) {
    throw "Operational root is missing. Run bootstrap first: $OperationsRoot"
}

foreach ($fileName in @("nightly-maintenance.ps1", "curation-queue.psm1")) {
    $source = Join-Path $PSScriptRoot $fileName
    $target = Join-Path $OperationsRoot $fileName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Nightly maintenance runtime file is missing: $source"
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

$entrypoint = Join-Path $OperationsRoot "nightly-maintenance.ps1"
$arguments = (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$entrypoint`""
)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$at = (Get-Date).Date.AddHours($Hour).AddMinutes($Minute)
$trigger = New-ScheduledTaskTrigger -Daily -At $at
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 48) `
    -MultipleInstances IgnoreNew `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description (
        "Run one sequential Local Knowledge Portal GPU maintenance pipeline daily at " +
        ("{0:D2}:{1:D2}; gpuq waits behind earlier reservations." -f $Hour, $Minute)
    ) `
    -Force | Out-Null

foreach ($legacy in @(
    "CurateKnowledge",
    "DeduplicateKnowledge",
    "PublishInformationFeed",
    "ValidateSemanticRecovery",
    "ReapGpuEmbeddingBatch"
)) {
    if (Get-ScheduledTask -TaskPath $TaskPath -TaskName $legacy -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskPath $TaskPath -TaskName $legacy | Out-Null
    }
}

$registered = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Write-Host (
    "Registered $TaskPath$TaskName daily at " +
    ("{0:D2}:{1:D2}; state {2}. Legacy model schedules are disabled." -f
        $Hour, $Minute, $registered.State)
)
