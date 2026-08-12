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
Import-Module (Join-Path $PSScriptRoot "curation-queue.psm1") -Force
$curation = Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/v1/knowledge/curation/status" -TimeoutSec 5
$modelName = if ($curation.model) { [string]$curation.model } else { "model-unresolved" }
$modelSlug = $modelName -replace '[^A-Za-z0-9._-]', '-'
$workload = "local-knowledge-portal-curate-cases-and-project-articles-model-$modelSlug"
if (-not $curation.enabled -and -not $curation.project_article_enabled) {
    Write-Host "Knowledge curation and project article editing are disabled; no GPU workload submitted."
    exit 0
}
$articleDue = if ($null -ne $curation.project_articles) {
    [int]$curation.project_articles.due
}
elseif ($curation.project_article_enabled) {
    1
}
else {
    0
}
if ([int]$curation.eligible_candidates -le 0 -and $articleDue -le 0) {
    Write-Host "No knowledge cases or project articles require editing; no GPU workload submitted."
    exit 0
}
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8790/api/status" -TimeoutSec 3
$existing = @(
    Get-CurationQueueEntries `
        -Status $status `
        -Workload @(
            $workload,
            "local-knowledge-portal-curate-cases-and-project-articles",
            "local-knowledge-portal-curation"
        )
)
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
    "bash", "/home/kutae/src/local-knowledge-portal/scripts/run-gpu-curation.sh"
)

& $gpuq.Source @command
if ($LASTEXITCODE -ne 0) {
    throw "gpuq submission failed with exit code $LASTEXITCODE"
}
