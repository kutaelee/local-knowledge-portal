[CmdletBinding()]
param(
  [string]$InstallDirectory = 'C:\Docker\local-knowledge-portal',
  [string]$TaskPath = '\LocalKnowledgePortal\',
  [string]$TaskName = 'ReapGpuEmbeddingBatch',
  [ValidateRange(15, 3600)][int]$PollSeconds = 60
)

$ErrorActionPreference = 'Stop'
$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
  Disable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
}

[pscustomobject]@{
  task = "$TaskPath$TaskName"
  state = 'retired'
  reason = 'The single Windows Ollama runtime has no temporary embedding container to reap.'
}
