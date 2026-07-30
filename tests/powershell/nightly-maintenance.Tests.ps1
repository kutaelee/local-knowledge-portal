$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Assert-Contains {
    param([string]$Value, [string]$Needle)
    if (-not $Value.Contains($Needle)) {
        throw "Expected source to contain: $Needle"
    }
}

Describe "Nightly GPU maintenance schedule" {
    It "registers one daily 00:30 task and disables legacy model schedules" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/install-nightly-maintenance-schedule.ps1"
        ) -Raw
        Assert-Contains $source '[int]$Hour = 0'
        Assert-Contains $source '[int]$Minute = 30'
        Assert-Contains $source 'New-ScheduledTaskTrigger -Daily'
        Assert-Contains $source '"CurateKnowledge"'
        Assert-Contains $source '"DeduplicateKnowledge"'
        Assert-Contains $source '"PublishInformationFeed"'
        Assert-Contains $source '"ValidateSemanticRecovery"'
    }

    It "waits for the gpuq reservation and identifies every exact model" {
        $source = Get-Content (Join-Path $repoRoot "scripts/nightly-maintenance.ps1") -Raw
        Assert-Contains $source '"qwen3-embedding:0.6b"'
        Assert-Contains $source '$curation.model'
        Assert-Contains $source '$feed.generator_model'
        Assert-Contains $source 'gpuq.Source wait --poll 15'
    }

    It "runs the three maintenance stages sequentially with cleanup" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/run-gpu-nightly-maintenance.sh"
        ) -Raw
        Assert-Contains $source 'nightly_semantic_maintenance'
        Assert-Contains $source 'nightly_generation_maintenance'
        Assert-Contains $source 'LKP_DEVELOPER_FEED_DAILY_SUMMARY_LAG_DAYS=1'
        Assert-Contains $source 'trap cleanup EXIT INT TERM'
    }
}
