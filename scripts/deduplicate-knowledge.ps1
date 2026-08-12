[CmdletBinding()]
param(
    [ValidateRange(4096, 65536)]
    [int]$VramMiB = 8192,
    [ValidateRange(60, 86400)]
    [int]$EstimatedSeconds = 900,
    [ValidateRange(0, 100)]
    [int]$Priority = 15
)

$ErrorActionPreference = "Stop"
$workload = "local-knowledge-portal-knowledge-dedup"
$dedup = Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/v1/knowledge/dedup/status" -TimeoutSec 5
if ([int]$dedup.pending_candidates -le 0) {
    Write-Host "No pending knowledge candidates require vector dedup."
    exit 0
}
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$existing = @($status.jobs.active) + @($status.jobs.queued) |
    Where-Object { $_.workload_key -eq $workload }
if ($existing.Count -gt 0) {
    Write-Host "Knowledge dedup is already queued or active: $($existing[0].id)"
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
    "sh", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-knowledge-dedup.sh"
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
