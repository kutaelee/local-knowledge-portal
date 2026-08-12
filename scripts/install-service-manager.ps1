[CmdletBinding()]
param(
  [string]$OperationalRoot = 'C:\Docker\local-knowledge-portal',
  [string]$RuntimeRoot = 'E:\Data\LocalKnowledgePortal',
  [string]$TaskName = '\Codex\Local Knowledge Service Manager',
  [string]$WslDistribution = 'Ubuntu',
  [switch]$RefreshRegistry
)

$ErrorActionPreference = 'Stop'
$sourceScript = Join-Path $PSScriptRoot 'host_service_manager.py'
$sourceStartScript = Join-Path $PSScriptRoot 'start-comfyui.ps1'
$sourceStopScript = Join-Path $PSScriptRoot 'stop-comfyui.ps1'
$managerRoot = Join-Path $OperationalRoot 'host-manager'
$configRoot = Join-Path $OperationalRoot 'config'
$configPath = Join-Path $configRoot 'managed-services.json'
$envPath = Join-Path $OperationalRoot '.env'
$installedScript = Join-Path $managerRoot 'service_manager.py'
$installedStartScript = Join-Path $managerRoot 'start-comfyui.ps1'
$installedStopScript = Join-Path $managerRoot 'stop-comfyui.ps1'
$launcherPath = Join-Path $managerRoot 'start-service-manager.ps1'
$logRoot = Join-Path $RuntimeRoot 'runtime\logs'
$logPath = Join-Path $logRoot 'host-service-manager.log'
$guardInstaller = Join-Path $PSScriptRoot 'install-service-manager-guard.ps1'

if (-not (Test-Path -LiteralPath $sourceScript)) {
  throw "Missing host service manager source: $sourceScript"
}
if (-not (Test-Path -LiteralPath $sourceStopScript)) {
  throw "Missing ComfyUI stop helper: $sourceStopScript"
}
if (-not (Test-Path -LiteralPath $sourceStartScript)) {
  throw "Missing ComfyUI start helper: $sourceStartScript"
}
if (-not (Test-Path -LiteralPath $envPath)) {
  throw "Missing operational environment file: $envPath"
}
if (-not (Test-Path -LiteralPath $guardInstaller)) {
  throw "Missing service manager guard installer: $guardInstaller"
}

New-Item -ItemType Directory -Force -Path $managerRoot, $configRoot, $logRoot | Out-Null
Copy-Item -LiteralPath $sourceScript -Destination $installedScript -Force
Copy-Item -LiteralPath $sourceStartScript -Destination $installedStartScript -Force
Copy-Item -LiteralPath $sourceStopScript -Destination $installedStopScript -Force

$envLines = [System.Collections.Generic.List[string]]::new()
Get-Content -LiteralPath $envPath | ForEach-Object { [void]$envLines.Add($_) }
$hasToken = $false
$hasBaseUrl = $false
foreach ($line in $envLines) {
  if ($line -match '^LKP_SERVICE_MANAGER_TOKEN=(.+)$' -and $Matches[1].Length -ge 32) {
    $hasToken = $true
  }
  if ($line -match '^LKP_SERVICE_MANAGER_BASE_URL=') {
    $hasBaseUrl = $true
  }
}
if (-not $hasToken -or -not $hasBaseUrl) {
  $backupRoot = Join-Path $OperationalRoot 'backups\env'
  New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
  $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
  Copy-Item -LiteralPath $envPath -Destination (Join-Path $backupRoot ".env.$stamp.bak")
  if (-not $hasToken) {
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    $token = [Convert]::ToBase64String($bytes)
    [void]$envLines.Add("LKP_SERVICE_MANAGER_TOKEN=$token")
  }
  if (-not $hasBaseUrl) {
    [void]$envLines.Add('LKP_SERVICE_MANAGER_BASE_URL=http://host.docker.internal:8791')
  }
  [System.IO.File]::WriteAllLines($envPath, $envLines, [System.Text.UTF8Encoding]::new($false))
}

function Get-ServiceLabel([string]$Project) {
  $labels = @{
    'local-knowledge-portal' = 'Local Knowledge Portal'
    'gpu-workload-scheduler' = 'GPU Queue Database'
    'unjeong-mining-web' = 'Unjeong Mining Web'
    'workstation-edge-ingress' = 'Workstation Edge Ingress'
    'local-voice-agent' = 'Local Voice Agent Database'
    'interstellar-drift' = 'Interstellar Drift Data Services'
    'workstation-databases' = 'Shared Workstation Databases'
  }
  if ($labels.ContainsKey($Project)) { return $labels[$Project] }
  return $Project
}

function Get-ServiceWebUrl([string]$Project) {
  $urls = @{
    'local-knowledge-portal' = 'http://127.0.0.1:3010/'
  }
  if ($urls.ContainsKey($Project)) { return $urls[$Project] }
  return $null
}

if ($RefreshRegistry -or -not (Test-Path -LiteralPath $configPath)) {
  $containers = docker ps --format '{{.ID}}'
  if ($LASTEXITCODE -ne 0) { throw 'Docker inventory failed.' }
  $groups = @{}
  foreach ($containerId in $containers) {
    $inspection = docker inspect $containerId | ConvertFrom-Json
    $labels = $inspection[0].Config.Labels
    $project = [string]$labels.'com.docker.compose.project'
    $configFiles = [string]$labels.'com.docker.compose.project.config_files'
    if (-not $project -or -not $configFiles) { continue }
    if (-not $groups.ContainsKey($project)) {
      $configFile = ($configFiles -split ',')[0]
      $groups[$project] = [ordered]@{
        id = "docker:$project"
        label = Get-ServiceLabel $project
        category = if ($project -match '^(workstation-|gpu-workload-scheduler)') {
          'infrastructure'
        } else { 'projects' }
        description = 'Registered service group managed by Docker Compose.'
        kind = 'docker_compose'
        control = $project -ne 'local-knowledge-portal'
        web_url = Get-ServiceWebUrl $project
        warning = if ($project -eq 'local-knowledge-portal') {
          'The portal cannot stop itself from this screen.'
        } else {
          'Stopping this group may stop its web, API, and data services together.'
        }
        config = [ordered]@{
          runner = if ($configFile.StartsWith('/')) { 'wsl' } else { 'windows' }
          distro = $WslDistribution
          project = $project
          config_file = $configFile
          services = @()
          action_timeout_seconds = 120
        }
      }
    }
  }

  $services = [System.Collections.Generic.List[object]]::new()
  foreach ($group in ($groups.Values | Sort-Object id)) { [void]$services.Add($group) }
  [void]$services.Add([ordered]@{
    id = 'comfyui'
    label = 'ComfyUI'
    category = 'ai'
    description = 'Lightweight image-generation interface; each prompt reserves GPU capacity through the shared queue.'
    kind = 'http_process'
    control = $true
    warning = 'Safe stop is rejected while generation jobs are running or queued.'
    health_url = 'http://127.0.0.1:8188/system_stats'
    web_url = 'http://127.0.0.1:8188/'
    config = [ordered]@{
      stop_guard = 'comfyui_queue_empty'
      queue_url = 'http://127.0.0.1:8188/queue'
      start_argv = @(
        'powershell.exe', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $installedStartScript, '-Port', '8188'
      )
      detached_child = $true
      stop_argv = @(
        'powershell.exe', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $installedStopScript, '-Port', '8188'
      )
      action_timeout_seconds = 150
    }
  })
  [void]$services.Add([ordered]@{
    id = 'ai-toolkit'
    label = 'AI-Toolkit'
    category = 'ai'
    description = 'Local training interface running as a WSL user service.'
    kind = 'wsl_systemd_user'
    control = $true
    warning = 'Check training job state before stopping this interface.'
    health_url = 'http://127.0.0.1:8675'
    web_url = 'http://127.0.0.1:8675/'
    config = [ordered]@{
      distro = $WslDistribution
      unit = 'ai-toolkit-ui.service'
      action_timeout_seconds = 60
    }
  })
  [void]$services.Add([ordered]@{
    id = 'ollama'
    label = 'Shared Ollama Model Server'
    category = 'ai'
    description = 'Single Windows Ollama daemon shared by Hermes and the portal.'
    kind = 'read_only'
    control = $false
    warning = 'The shared model server is protected from application-level stop controls.'
    health_url = 'http://127.0.0.1:11434/api/version'
    config = @{}
  })
  [void]$services.Add([ordered]@{
    id = 'gpu-scheduler-host'
    label = 'GPU Workload Scheduler'
    category = 'infrastructure'
    description = 'Manages reservations and safety headroom for long-running GPU work.'
    kind = 'read_only'
    control = $false
    warning = 'The GPU safety boundary is protected from portal stop controls.'
    health_url = 'http://127.0.0.1:8790/api/health'
    web_url = 'http://127.0.0.1:8790/'
    config = @{}
  })
  $registry = [ordered]@{ schema_version = 1; services = $services }
  [System.IO.File]::WriteAllText(
    $configPath,
    ($registry | ConvertTo-Json -Depth 12),
    [System.Text.UTF8Encoding]::new($false)
  )
}

$registryPayload = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$comfyService = $registryPayload.services | Where-Object { $_.id -eq 'comfyui' } |
  Select-Object -First 1
if ($null -ne $comfyService) {
  $desiredComfyConfig = [ordered]@{
    stop_guard = 'comfyui_queue_empty'
    queue_url = 'http://127.0.0.1:8188/queue'
    start_argv = @(
      'powershell.exe', '-NoProfile', '-NonInteractive',
      '-ExecutionPolicy', 'Bypass',
      '-File', $installedStartScript, '-Port', '8188'
    )
    detached_child = $true
    stop_argv = @(
      'powershell.exe', '-NoProfile', '-NonInteractive',
      '-ExecutionPolicy', 'Bypass',
      '-File', $installedStopScript, '-Port', '8188'
    )
    action_timeout_seconds = 150
  }
  $currentConfigJson = $comfyService.config | ConvertTo-Json -Depth 8 -Compress
  $desiredConfigJson = $desiredComfyConfig | ConvertTo-Json -Depth 8 -Compress
  $needsComfyUpgrade = (
    $comfyService.kind -ne 'http_process' -or
    $comfyService.description -ne (
      'Lightweight image-generation interface; each prompt reserves GPU capacity through the shared queue.'
    ) -or
    $currentConfigJson -ne $desiredConfigJson
  )
  if ($needsComfyUpgrade) {
    $registryBackupRoot = Join-Path $OperationalRoot 'backups\service-registry'
    New-Item -ItemType Directory -Force -Path $registryBackupRoot | Out-Null
    $registryStamp = Get-Date -Format 'yyyyMMddTHHmmss'
    Copy-Item -LiteralPath $configPath -Destination (
      Join-Path $registryBackupRoot "managed-services.$registryStamp.json"
    )
    $comfyService.kind = 'http_process'
    $comfyService.description = (
      'Lightweight image-generation interface; each prompt reserves GPU capacity through the shared queue.'
    )
    $comfyService.config = [pscustomobject]$desiredComfyConfig
    [System.IO.File]::WriteAllText(
      $configPath,
      ($registryPayload | ConvertTo-Json -Depth 12),
      [System.Text.UTF8Encoding]::new($false)
    )
  }
}

$launcher = @'
$ErrorActionPreference = 'Stop'
$root = 'C:\Docker\local-knowledge-portal'
$envPath = Join-Path $root '.env'
$scriptPath = Join-Path $root 'host-manager\service_manager.py'
$logPath = 'E:\Data\LocalKnowledgePortal\runtime\logs\host-service-manager.log'
$line = Get-Content -LiteralPath $envPath |
  Where-Object { $_ -match '^LKP_SERVICE_MANAGER_TOKEN=' } |
  Select-Object -Last 1
if (-not $line) { throw 'LKP_SERVICE_MANAGER_TOKEN is not configured.' }
$env:LKP_SERVICE_MANAGER_TOKEN = $line.Substring($line.IndexOf('=') + 1)
$env:LKP_SERVICE_MANAGER_CONFIG = Join-Path $root 'config\managed-services.json'
$env:LKP_SERVICE_MANAGER_HOST = '127.0.0.1'
$env:LKP_SERVICE_MANAGER_PORT = '8791'
& py.exe -3.13 -u $scriptPath *>> $logPath
exit $LASTEXITCODE
'@
[System.IO.File]::WriteAllText(
  $launcherPath,
  $launcher,
  [System.Text.UTF8Encoding]::new($true)
)

$taskPath = Split-Path -Parent $TaskName
$taskLeaf = Split-Path -Leaf $TaskName
if (-not $taskPath.EndsWith('\')) { $taskPath += '\' }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
  "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`""
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 5 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited
Register-ScheduledTask `
  -TaskName $taskLeaf `
  -TaskPath $taskPath `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'Allow-listed loopback service control for Local Knowledge Portal.' `
  -Force | Out-Null

$existing = Get-NetTCPConnection -State Listen -LocalPort 8791 -ErrorAction SilentlyContinue
if (-not $existing) {
  Start-ScheduledTask -TaskName $taskLeaf -TaskPath $taskPath
}

$deadline = (Get-Date).AddSeconds(20)
do {
  Start-Sleep -Milliseconds 500
  try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8791/api/health' -TimeoutSec 2
  } catch {
    $health = $null
  }
} until ($health.status -eq 'healthy' -or (Get-Date) -gt $deadline)
if ($health.status -ne 'healthy') {
  throw "Host service manager did not become healthy. Check $logPath"
}

$guard = & $guardInstaller `
  -OperationalRoot $OperationalRoot `
  -ManagerTaskName $TaskName

[pscustomobject]@{
  status = 'healthy'
  endpoint = 'http://127.0.0.1:8791'
  registry = $configPath
  task = $TaskName
  guard_task = $guard.guard_task
  token = 'configured-and-not-displayed'
}
