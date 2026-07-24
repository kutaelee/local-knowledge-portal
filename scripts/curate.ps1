[CmdletBinding()]
param(
    [ValidateRange(2048, 65536)]
    [int]$VramMiB = 8192,
    [ValidateRange(1, 86400)]
    [int]$EstimatedSeconds = 1800,
    [ValidateRange(0, 100)]
    [int]$Priority = 40
)

$ErrorActionPreference = "Stop"
$workload = "local-knowledge-portal-curation"
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$existing = @($status.jobs.active) + @($status.jobs.queued) |
    Where-Object { $_.workload_key -eq $workload }
if ($existing.Count -gt 0) {
    Write-Host "Curation is already queued or active: $($existing[0].id)"
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
    "docker", "compose",
    "--env-file", "/mnt/c/Docker/local-knowledge-portal/.env",
    "-f", "/mnt/c/Docker/local-knowledge-portal/compose.yaml",
    "--profile", "manual-curation",
    "run", "--rm", "--no-deps",
    "knowledge-curator"
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
