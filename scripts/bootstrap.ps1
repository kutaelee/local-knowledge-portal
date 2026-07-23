[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Required = @(
  'E:\LocalKnowledgePortal\postgres',
  'E:\LocalKnowledgePortal\models',
  'E:\LocalKnowledgePortal\cache',
  'E:\LocalKnowledgePortal\ingest',
  'E:\LocalKnowledgePortal\runtime\logs',
  'E:\LocalKnowledgePortal\runtime\pid',
  'E:\LocalKnowledgePortal\vault',
  'E:\LocalKnowledgePortal\exports',
  'D:\Backups\LocalKnowledgePortal\database',
  'D:\Backups\LocalKnowledgePortal\vault',
  'D:\Backups\LocalKnowledgePortal\config',
  'D:\Backups\LocalKnowledgePortal\manifests',
  'D:\Backups\LocalKnowledgePortal\restore-tests'
)
foreach ($Path in $Required) {
  if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
}
if (-not (Test-Path -LiteralPath (Join-Path $Repo '.env'))) {
  $PasswordBytes = New-Object byte[] 32
  $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $Random.GetBytes($PasswordBytes) } finally { $Random.Dispose() }
  $Password = [BitConverter]::ToString($PasswordBytes).Replace('-', '').ToLowerInvariant()
  $Content = Get-Content -Raw -LiteralPath (Join-Path $Repo '.env.example')
  $Content = $Content.Replace('CHANGE_ME', $Password)
  [IO.File]::WriteAllText(
    (Join-Path $Repo '.env'),
    $Content,
    (New-Object Text.UTF8Encoding($false))
  )
}
$EnvContent = Get-Content -Raw -LiteralPath (Join-Path $Repo '.env')
[IO.File]::WriteAllText(
  (Join-Path $Repo '.env'),
  $EnvContent,
  (New-Object Text.UTF8Encoding($false))
)
if (-not (Test-Path -LiteralPath (Join-Path $Repo 'config\source-roots.yaml'))) {
  Copy-Item -LiteralPath (Join-Path $Repo 'config\source-roots.example.yaml') `
    -Destination (Join-Path $Repo 'config\source-roots.yaml')
}
if (-not (Test-Path -LiteralPath (Join-Path $Repo 'config\settings.yaml'))) {
  Copy-Item -LiteralPath (Join-Path $Repo 'config\settings.example.yaml') `
    -Destination (Join-Path $Repo 'config\settings.yaml')
}
uv sync --project $Repo
pnpm --dir $Repo install --frozen-lockfile=$false
Write-Host 'Bootstrap complete. Review config/source-roots.yaml before scanning.'
