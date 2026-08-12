[CmdletBinding()]
param(
  [string]$Transcript,
  [switch]$Watch,
  [switch]$ImportExisting,
  [switch]$Index
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Arguments = @('run', '--project', $Repo, 'python', '-m', 'lkp_indexer.codex_capture')
if ($Transcript) {
  $Arguments += @('--transcript', $Transcript)
} elseif ($ImportExisting) {
  $Arguments += '--import-existing'
} else {
  $Arguments += @('--watch', '--enrich')
}
if ($Index) { $Arguments += '--index' }
& uv @Arguments
