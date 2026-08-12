$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Assert-Contains {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][string]$Needle
    )
    if (-not $Value.Contains($Needle)) {
        throw "Expected launcher source to contain: $Needle"
    }
}

Describe "GPU curation launcher" {
    It "submits only the reservation-owned wrapper, not a dependency-less curator" {
        $script = Get-Content (Join-Path $repoRoot "scripts/curate.ps1") -Raw
        Assert-Contains -Value $script -Needle "run-gpu-curation.sh"
        if ($script -match '"knowledge-curator"\s*\)') {
            throw "The entrypoint must not call the dependency-less curator directly"
        }
    }

    It "starts, verifies, and tears down only its own generation runtime" {
        $wrapper = Get-Content (Join-Path $repoRoot "scripts/run-gpu-curation.sh") -Raw
        Assert-Contains -Value $wrapper -Needle "started_generation=false"
        Assert-Contains -Value $wrapper -Needle "refusing curation: ollama-generation is already"
        Assert-Contains -Value $wrapper -Needle "compose up -d --no-build ollama-generation"
        Assert-Contains -Value $wrapper -Needle "generation Ollama did not become healthy"
        Assert-Contains -Value $wrapper -Needle "compose run --rm --no-deps ollama-generation-model"
        Assert-Contains -Value $wrapper -Needle "compose --profile manual-curation run --rm --no-deps knowledge-curator"
        Assert-Contains -Value $wrapper -Needle "compose stop ollama-generation"
    }
}
