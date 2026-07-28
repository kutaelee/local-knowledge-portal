[CmdletBinding()]
param(
    # qwen3-embedding:0.6b measured about 6.2 GiB with the service baseline.
    # Keep the same 8 GiB reservation as the reindex; validation must not
    # understate its real VRAM requirement merely because it is short-lived.
    [ValidateRange(8192, 65536)]
    [int]$VramMiB = 8192,
    [ValidateRange(60, 86400)]
    [int]$EstimatedSeconds = 600,
    [ValidateRange(0, 100)]
    [int]$Priority = 35,
    [string]$OperationalEnvPath = "C:\Docker\local-knowledge-portal\.env"
)

$ErrorActionPreference = "Stop"
$workload = "local-knowledge-portal-semantic-validation"
$reindexWorkload = "local-knowledge-portal-embedding-reindex"
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$current = @($status.jobs.active) + @($status.jobs.queued)
$existing = @($current |
    Where-Object { $_.workload_key -eq $workload })
if ($existing.Count -gt 0) {
    Write-Host "Semantic recovery validation is already queued or active: $($existing[0].id)"
    exit 0
}

$pendingReindex = @($current | Where-Object { $_.workload_key -eq $reindexWorkload })
if ($pendingReindex.Count -gt 0) {
    Write-Host "Semantic recovery validation waits for reindex: $($pendingReindex[0].id)"
    exit 0
}

$latestReindex = @($status.jobs.completed |
    Where-Object { $_.workload_key -eq $reindexWorkload } |
    Sort-Object submitted_at -Descending |
    Select-Object -First 1)
if ($latestReindex.Count -eq 0 -or $latestReindex[0].status -notin @("succeeded", "completed")) {
    Write-Host "Semantic recovery validation requires a succeeded reindex job."
    exit 0
}

$localOperationsEnv = Join-Path $PSScriptRoot ".env"
$operationsEnv = if (Test-Path -LiteralPath $localOperationsEnv -PathType Leaf) {
    $localOperationsEnv
} else {
    $OperationalEnvPath
}
$dataRootLine = if (Test-Path -LiteralPath $operationsEnv -PathType Leaf) {
    Select-String -LiteralPath $operationsEnv -Pattern "^LKP_DATA_ROOT=" |
        Select-Object -First 1
}
if (-not $dataRootLine) {
    throw "LKP_DATA_ROOT is required in the operational environment file."
}
$dataRoot = $dataRootLine.Line.Substring("LKP_DATA_ROOT=".Length).Trim()
$snapshotPath = Join-Path $dataRoot "runtime\embedding-recovery-validation.json"
if (Test-Path -LiteralPath $snapshotPath -PathType Leaf) {
    try {
        $snapshot = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json -ErrorAction Stop
        $checkedAt = [DateTimeOffset]::Parse([string]$snapshot.checked_at)
        $finishedAt = [DateTimeOffset]::Parse([string]$latestReindex[0].finished_at)
        if ($checkedAt -ge $finishedAt) {
            Write-Host "Semantic recovery already validated after reindex: $($latestReindex[0].id)"
            exit 0
        }
    } catch {
        # A malformed/stale snapshot must not be treated as proof. The bounded
        # gpuq probe can replace it after the completed reindex.
        Write-Warning "Recovery validation snapshot is not usable; a new probe may be submitted."
    }
}

$gpuq = Get-Command "gpuq" -ErrorAction Stop
$command = @(
    "run",
    "--vram", [string]$VramMiB,
    "--eta", [string]$EstimatedSeconds,
    "--max-runtime", "1800",
    "--priority", [string]$Priority,
    "--agent", "local-knowledge-portal",
    "--workload", $workload,
    "--",
    "wsl.exe", "-d", "Ubuntu", "--",
    "sh", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-semantic-recovery-probe.sh"
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
