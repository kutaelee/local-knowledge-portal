[CmdletBinding(DefaultParameterSetName = 'Watch')]
param(
  [Parameter(ParameterSetName = 'Once')]
  [string]$Transcript,
  [Parameter(ParameterSetName = 'Watch')]
  [switch]$Watch,
  [switch]$Index
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Arguments = @('run', '--project', $Repo, 'python', '-m', 'lkp_indexer.codex_capture')
if ($PSCmdlet.ParameterSetName -eq 'Once') {
  $Arguments += @('--transcript', $Transcript)
} else {
  $Arguments += @('--watch', '--codex-home', (Join-Path $env:USERPROFILE '.codex'))
}
if ($Index) { $Arguments += '--index' }
& uv @Arguments
