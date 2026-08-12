[CmdletBinding()]
param(
    # qwen3-embedding:0.6b Q8_0 used about 6.2 GiB above the service baseline
    # at batch two. The RTX 5090 batch-four profile reserves 10 GiB.
    [ValidateRange(10240, 65536)]
    [int]$VramMiB = 10240,
    [ValidateRange(60, 86400)]
    [int]$EstimatedSeconds = 1800,
    [ValidateRange(0, 100)]
    [int]$Priority = 30,
    [ValidateRange(1, 100000)]
    [int]$Limit = 500
)

$ErrorActionPreference = "Stop"
$workload = "local-knowledge-portal-embedding-reindex-model-qwen3-embedding-0.6b-q8_0"
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
