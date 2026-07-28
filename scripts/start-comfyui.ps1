[CmdletBinding()]
param(
  [ValidateRange(1, 65535)]
  [int]$Port = 8188,
  [ValidateRange(10, 300)]
  [int]$ReadyTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$root = 'E:\AI\Apps\ComfyUI'
$python = Join-Path $root '.venv\Scripts\python.exe'
$main = Join-Path $root 'main.py'
$runtimeLogRoot = 'E:\Data\LocalKnowledgePortal\runtime\logs'
$healthUrl = "http://127.0.0.1:$Port/system_stats"

if (-not (Test-Path -LiteralPath $python)) {
  throw "Missing ComfyUI Python environment: $python"
}
if (-not (Test-Path -LiteralPath $main)) {
  throw "Missing ComfyUI entrypoint: $main"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 3
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
      Write-Output "ComfyUI is already healthy on 127.0.0.1:$Port."
      exit 0
    }
  } catch {
    throw "Port $Port is already owned by a process that is not a healthy ComfyUI instance."
  }
}

New-Item -ItemType Directory -Force -Path $runtimeLogRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$stdoutPath = Join-Path $runtimeLogRoot "comfyui-$stamp.stdout.log"
$stderrPath = Join-Path $runtimeLogRoot "comfyui-$stamp.stderr.log"

# The server itself is a lightweight control plane. Do not set
# COMFYUI_GPUQ_SERVER_MANAGED: the bridge must reserve GPU capacity per prompt.
$env:HF_HOME = 'E:\Cache\HuggingFace'
$env:HUGGINGFACE_HUB_CACHE = 'E:\Cache\HuggingFace\hub'
$env:TORCH_HOME = 'E:\Cache\Torch'
$env:PIP_CACHE_DIR = 'E:\Cache\Pip'
$env:UV_CACHE_DIR = 'E:\Cache\Uv'
$env:PYTHONUTF8 = '1'
$env:GIT_PYTHON_GIT_EXECUTABLE = 'C:\Program Files\Git\cmd\git.exe'
$env:Path = "C:\Program Files\Git\cmd;$env:Path"
Remove-Item Env:COMFYUI_GPUQ_SERVER_MANAGED -ErrorAction SilentlyContinue

$arguments = @(
  $main,
  '--enable-manager',
  '--extra-model-paths-config', (Join-Path $root 'extra_model_paths.yaml'),
  '--input-directory', 'E:\AI\Input',
  '--output-directory', 'E:\AI\Outputs',
  '--temp-directory', 'E:\AI\Temp\ComfyUI',
  '--user-directory', 'E:\Data\AppData\ComfyUI',
  '--database-url', 'sqlite:///E:/Data/AppData/ComfyUI/comfyui.db',
  '--listen', '127.0.0.1',
  '--port', "$Port",
  '--disable-auto-launch'
)

$process = Start-Process `
  -FilePath $python `
  -ArgumentList $arguments `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutPath `
  -RedirectStandardError $stderrPath `
  -PassThru

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
  if ($process.HasExited) {
    $detail = if (Test-Path -LiteralPath $stderrPath) {
      (Get-Content -LiteralPath $stderrPath -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    } else {
      ''
    }
    throw "ComfyUI exited before readiness with code $($process.ExitCode). $detail"
  }
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 3
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
      Write-Output "ComfyUI ready on 127.0.0.1:$Port (PID $($process.Id)); prompts reserve GPU through gpuq."
      exit 0
    }
  } catch {
    # Startup probes are expected to fail until aiohttp begins listening.
  }
  Start-Sleep -Seconds 1
}

if (-not $process.HasExited) {
  Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
  $process.WaitForExit(5000) | Out-Null
}
throw "ComfyUI did not become ready within $ReadyTimeoutSeconds seconds; the task-owned startup process was stopped."
