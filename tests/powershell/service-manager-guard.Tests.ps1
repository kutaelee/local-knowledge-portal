$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Assert-Contains {
    param([string]$Value, [string]$Needle)
    if (-not $Value.Contains($Needle)) {
        throw "Expected source to contain: $Needle"
    }
}

Describe "Host service manager control-plane guard" {
    It "repairs only the exact manager task and honors explicit maintenance" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/ensure-service-manager.ps1"
        ) -Raw
        Assert-Contains $source "Test-Path -LiteralPath `$MaintenanceMarker"
        Assert-Contains $source "Enable-ScheduledTask -TaskPath `$TaskPath -TaskName `$TaskName"
        Assert-Contains $source "Start-ScheduledTask -TaskPath `$TaskPath -TaskName `$TaskName"
        Assert-Contains $source "http://127.0.0.1:8791/api/health"
        if ($source.Contains("Get-ScheduledTask |")) {
            throw "The guard must not enumerate or mutate unrelated scheduled tasks."
        }
    }

    It "installs an independent periodic CPU-only guard" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/install-service-manager-guard.ps1"
        ) -Raw
        Assert-Contains $source "Local Knowledge Service Manager Guard"
        Assert-Contains $source "-RepetitionInterval (New-TimeSpan -Minutes `$IntervalMinutes)"
        Assert-Contains $source "maintenance.disabled"
        Assert-Contains $source "CPU-only Local Knowledge control plane"
        Assert-Contains $source "'-TaskPath', `$managerIdentity.Path"
    }

    It "keeps the guard in the main service-manager installation path" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/install-service-manager.ps1"
        ) -Raw
        Assert-Contains $source "install-service-manager-guard.ps1"
        Assert-Contains $source "guard_task = `$guard.guard_task"
    }

    It "deploys the host manager with an exact-task maintenance boundary and rollback" {
        $source = Get-Content (
            Join-Path $repoRoot "scripts/deploy-host-service-manager.ps1"
        ) -Raw
        Assert-Contains $source "maintenance.disabled"
        Assert-Contains $source "service_manager.`$stamp.py.bak"
        Assert-Contains $source "Stop-ScheduledTask -TaskPath `$TaskPath -TaskName `$TaskName"
        Assert-Contains $source "Disable-ScheduledTask -TaskPath `$TaskPath -TaskName `$TaskName"
        Assert-Contains $source "Enable-ScheduledTask -TaskPath `$TaskPath -TaskName `$TaskName"
        Assert-Contains $source "Expected exactly one host service manager listener."
        Assert-Contains $source "Refusing to stop an unowned or unexpected process on port 8791."
        Assert-Contains $source "Stop-Process -Id `$process.ProcessId -Force"
        Assert-Contains $source "Copy-Item -LiteralPath `$backup -Destination `$installed -Force"
        Assert-Contains $source "http://127.0.0.1:8791/api/health"
        Assert-Contains $source "`$health.capabilities -contains 'loopback_web_url'"
    }
}
