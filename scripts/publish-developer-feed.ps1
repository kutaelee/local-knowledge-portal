[CmdletBinding()]
param(
    [ValidateRange(4096, 65536)]
    [int]$VramMiB = 12288,
    [ValidateRange(1, 86400)]
    [int]$EstimatedSeconds = 900,
    [ValidateRange(0, 100)]
    [int]$Priority = 40
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "curation-queue.psm1") -Force
$feed = Invoke-RestMethod `
    -Uri "http://127.0.0.1:8010/api/v1/developer-feed/status" `
    -TimeoutSec 10
if (-not $feed.enabled) {
    Write-Host "Developer feed is disabled; no GPU workload submitted."
    exit 0
}
if ([int]$feed.pending_sources -le 0 -and -not [bool]$feed.daily_due) {
    Write-Host "No newly embedded information and no daily summary due; no model loaded."
    exit 0
}

$modelName = if ($feed.generator_model) {
    [string]$feed.generator_model
}
else {
    "model-unresolved"
}
$modelSlug = $modelName -replace '[^A-Za-z0-9._-]', '-'
$workload = "local-knowledge-portal-write-information-feed-model-$modelSlug"
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$existing = @(
    Get-CurationQueueEntries `
        -Status $status `
        -Workload @(
            $workload,
            "local-knowledge-portal-write-information-feed"
        )
)
if ($existing.Count -gt 0) {
    Write-Host "Information feed is already queued or active: $($existing[0].id)"
    exit 0
}

$gpuq = Get-Command "gpuq" -ErrorAction Stop
$command = @(
    "run",
    "--vram", [string]$VramMiB,
    "--eta", [string]$EstimatedSeconds,
    "--max-runtime", "3600",
    "--priority", [string]$Priority,
    "--agent", "local-knowledge-portal",
    "--workload", $workload,
    "--",
    "wsl.exe", "-d", "Ubuntu", "--",
    "bash", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-developer-feed.sh"
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
