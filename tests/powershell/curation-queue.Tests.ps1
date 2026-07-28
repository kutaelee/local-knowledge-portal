[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$modulePath = Join-Path $PSScriptRoot "..\..\scripts\curation-queue.psm1"
Import-Module $modulePath -Force

function New-QueueJob {
    param([string]$Workload)
    [pscustomobject]@{ workload_key = $Workload; id = [guid]::NewGuid().ToString() }
}

$workload = "local-knowledge-portal-curation"
$singleQueuedStatus = [pscustomobject]@{
    jobs = [pscustomobject]@{
        active = @()
        queued = New-QueueJob $workload
    }
}
$singleQueued = @(Get-CurationQueueEntries -Status $singleQueuedStatus -Workload $workload)
if ($singleQueued.Count -ne 1) {
    throw "Expected exactly one queued curation job, got $($singleQueued.Count)"
}

# The production entrypoint assigns this exact expression before checking
# `.Count`; keep the single-object PowerShell unrolling regression covered.
$entrypointStyleSingle = @(Get-CurationQueueEntries -Status $singleQueuedStatus -Workload $workload)
if ($entrypointStyleSingle.Count -ne 1) {
    throw "Entry-point assignment lost the single queued job"
}

$activeAndOtherQueuedStatus = [pscustomobject]@{
    jobs = [pscustomobject]@{
        active = New-QueueJob $workload
        queued = @((New-QueueJob "unrelated-workload"), (New-QueueJob $workload))
    }
}
$activeAndQueued = @(
    Get-CurationQueueEntries -Status $activeAndOtherQueuedStatus -Workload $workload
)
if ($activeAndQueued.Count -ne 2) {
    throw "Expected active and queued curation jobs, got $($activeAndQueued.Count)"
}

$none = @(Get-CurationQueueEntries -Status ([pscustomobject]@{
    jobs = [pscustomobject]@{ active = @(); queued = @() }
}) -Workload $workload)
if ($none.Count -ne 0) {
    throw "Expected no curation jobs, got $($none.Count)"
}

[pscustomobject]@{
    single_queued = $singleQueued.Count
    active_and_queued = $activeAndQueued.Count
    no_match = $none.Count
}
