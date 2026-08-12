Set-StrictMode -Version Latest

function Get-CurationQueueEntries {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Status,
        [Parameter(Mandatory)]
        [string[]]$Workload
    )

    # Both properties can be a single PSCustomObject or an array. Force the
    # combined match back to an array so `.Count` stays correct for exactly one
    # active/queued entry as well as zero or many entries.
    return @(
        (@($Status.jobs.active) + @($Status.jobs.queued)) |
            Where-Object { $Workload -contains $_.workload_key }
    )
}

Export-ModuleMember -Function Get-CurationQueueEntries
