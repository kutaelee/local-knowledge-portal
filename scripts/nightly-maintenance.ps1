[CmdletBinding()]
param(
    [ValidateRange(12288, 65536)]
    [int]$VramMiB = 12288,
    [ValidateRange(300, 86400)]
    [int]$EstimatedSeconds = 3600,
    [ValidateRange(0, 100)]
    [int]$Priority = 30
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "curation-queue.psm1") -Force

$curation = Invoke-RestMethod `
    -Uri "http://127.0.0.1:8010/api/v1/knowledge/curation/status" `
    -TimeoutSec 10
$feed = Invoke-RestMethod `
    -Uri "http://127.0.0.1:8010/api/v1/developer-feed/status" `
    -TimeoutSec 10
$models = @(
    "qwen3-embedding:0.6b",
    [string]$curation.model,
    [string]$feed.generator_model
) | Where-Object { $_ } | Select-Object -Unique
$modelSlug = ($models -join "-") -replace '[^A-Za-z0-9._-]', '-'
$workload = "local-knowledge-portal-nightly-maintenance-model-$modelSlug"

$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 5
$existing = @(
    Get-CurationQueueEntries `
        -Status $status `
        -Workload @($workload, "local-knowledge-portal-nightly-maintenance")
)
if ($existing.Count -gt 0) {
    Write-Host "Nightly maintenance is already queued or active: $($existing[0].id)"
    exit 0
}

$gpuq = Get-Command "gpuq" -ErrorAction Stop
$command = @(
    "run",
    "--vram", [string]$VramMiB,
    "--eta", [string]$EstimatedSeconds,
    "--max-runtime", "28800",
    "--priority", [string]$Priority,
    "--agent", "local-knowledge-portal",
    "--workload", $workload,
    "--",
    "wsl.exe", "-d", "Ubuntu", "--",
    "sh", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-nightly-maintenance.sh"
)
$jobId = (& $gpuq.Source @command | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $jobId -notmatch '^[0-9a-f-]{36}$') {
    throw "gpuq submission did not return a valid job ID."
}
Write-Host "Nightly maintenance queued as $jobId; waiting behind earlier GPU work."

& $gpuq.Source wait --poll 15 $jobId
if ($LASTEXITCODE -ne 0) {
    throw "Nightly maintenance failed: $jobId"
}
