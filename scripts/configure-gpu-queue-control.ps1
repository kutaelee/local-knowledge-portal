[CmdletBinding()]
param(
    [string]$SchedulerConfigPath = "C:\Dev\Repos\gpu-workload-scheduler\.runtime\config.json",
    [string]$PortalEnvPath = "C:\Docker\local-knowledge-portal\.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SchedulerConfigPath)) {
    throw "GPU scheduler config was not found: $SchedulerConfigPath"
}

$schedulerConfig = Get-Content -LiteralPath $SchedulerConfigPath -Raw | ConvertFrom-Json
$token = [string]$schedulerConfig.api_token
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "GPU scheduler config does not contain an API token."
}

$obsoletePortalCleanupKeys = @(
    "local-knowledge-portal-curation",
    "local-knowledge-portal-embedding-reindex",
    "local-knowledge-portal-knowledge-dedup"
)
$cleanupCommands = [ordered]@{}
if ($null -ne $schedulerConfig.cleanup_commands) {
    foreach ($property in $schedulerConfig.cleanup_commands.PSObject.Properties) {
        if ($property.Name -notin $obsoletePortalCleanupKeys) {
            $cleanupCommands[$property.Name] = @($property.Value)
        }
    }
}
$existingCleanup = if ($null -ne $schedulerConfig.cleanup_commands) {
    $schedulerConfig.cleanup_commands | ConvertTo-Json -Depth 8 -Compress
} else {
    ""
}
$desiredCleanup = $cleanupCommands | ConvertTo-Json -Depth 8 -Compress
if ($existingCleanup -cne $desiredCleanup) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item -LiteralPath $SchedulerConfigPath -Destination "$SchedulerConfigPath.$timestamp.bak"
    if ($null -eq $schedulerConfig.cleanup_commands) {
        $schedulerConfig | Add-Member -NotePropertyName cleanup_commands -NotePropertyValue $cleanupCommands
    } else {
        $schedulerConfig.cleanup_commands = $cleanupCommands
    }
    $schedulerDirectory = Split-Path -Parent $SchedulerConfigPath
    $schedulerTemporary = Join-Path $schedulerDirectory (".config.gpuq.$PID.tmp")
    $schedulerJson = $schedulerConfig | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText(
        $schedulerTemporary,
        $schedulerJson + "`r`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $schedulerTemporary -Destination $SchedulerConfigPath -Force
    Write-Output "Removed obsolete portal container cleanup while preserving unrelated entries."
}

$portalDirectory = Split-Path -Parent $PortalEnvPath
if (-not (Test-Path -LiteralPath $portalDirectory)) {
    throw "Portal environment directory was not found: $portalDirectory"
}

$original = if (Test-Path -LiteralPath $PortalEnvPath) {
    Get-Content -LiteralPath $PortalEnvPath -Raw
} else {
    ""
}
$line = "LKP_GPU_SCHEDULER_CONTROL_TOKEN=$token"
$updated = if ($original -match '(?m)^LKP_GPU_SCHEDULER_CONTROL_TOKEN=.*$') {
    [regex]::Replace($original, '(?m)^LKP_GPU_SCHEDULER_CONTROL_TOKEN=.*$', $line)
} else {
    ($original.TrimEnd("`r", "`n") + "`r`n" + $line + "`r`n")
}

if ($updated -ceq $original) {
    Write-Output "GPU queue control token is already configured."
    exit 0
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (Test-Path -LiteralPath $PortalEnvPath) {
    Copy-Item -LiteralPath $PortalEnvPath -Destination "$PortalEnvPath.$timestamp.bak" -ErrorAction Stop
}
$temporary = Join-Path $portalDirectory (".env.gpuq.$PID.tmp")
[System.IO.File]::WriteAllText($temporary, $updated, [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $PortalEnvPath -Force
Write-Output "Configured the portal's server-side GPU queue control token. Existing token value was not displayed."
