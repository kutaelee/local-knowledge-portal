[CmdletBinding()]
param(
    # qwen3-embedding:0.6b used about 6.2 GiB alongside the service runtime
    # in a measured production backfill. Reserve a conservative 8 GiB so the
    # scheduler never admits this task on the old 2 GiB estimate.
    [ValidateRange(8192, 65536)]
    [int]$VramMiB = 8192,
    [ValidateRange(60, 86400)]
    [int]$EstimatedSeconds = 1800,
    [ValidateRange(0, 100)]
    [int]$Priority = 30,
    [ValidateRange(1, 100000)]
    [int]$Limit = 500
)

$ErrorActionPreference = "Stop"
$workload = "local-knowledge-portal-embedding-reindex"
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$existing = @($status.jobs.active) + @($status.jobs.queued) |
    Where-Object { $_.workload_key -eq $workload }
if ($existing.Count -gt 0) {
    Write-Host "Embedding reindex is already queued or active: $($existing[0].id)"
    exit 0
}

$gpuq = Get-Command "gpuq" -ErrorAction Stop
$command = @(
    "run",
    "--vram", [string]$VramMiB,
    "--eta", [string]$EstimatedSeconds,
    "--max-runtime", "21600",
    "--priority", [string]$Priority,
    "--agent", "local-knowledge-portal",
    "--workload", $workload,
    "--",
    "wsl.exe", "-d", "Ubuntu", "--",
    "sh", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-embedding-reindex.sh",
    "--limit", [string]$Limit
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
