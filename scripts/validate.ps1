[CmdletBinding()]
param([switch]$Integration)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot

# Windows uv cannot safely create or execute a virtual environment in a WSL
# UNC repository. Keep the repository and every native dependency on the same
# side of the OS boundary.
if ($Repo -match '^\\\\wsl(?:\.localhost)?\\([^\\]+)\\(.*)$') {
    $distribution = $Matches[1]
    $linuxRepo = "/" + ($Matches[2] -replace '\\', '/')
    $quotedRepo = $linuxRepo.Replace("'", "'`"`"'`"`"'")
    $pytestArgs = if ($Integration) { "" } else { "-m 'not integration'" }
    $command = (
        "cd '$quotedRepo' && " +
        "uv run --frozen ruff check services tests && " +
        "uv run --frozen pytest $pytestArgs && " +
        "corepack pnpm --filter @lkp/web build"
    )
    & wsl.exe -d $distribution -- bash -lc $command
    if ($LASTEXITCODE -ne 0) {
        throw "WSL validation failed with exit code $LASTEXITCODE"
    }
    exit 0
}

Push-Location $Repo
try {
    uv run --frozen ruff check services tests
    if ($LASTEXITCODE -ne 0) { throw "ruff failed with exit code $LASTEXITCODE" }
    uv run --frozen pytest $(if ($Integration) { @() } else { @('-m','not integration') })
    if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }
    pnpm --filter '@lkp/web' build
    if ($LASTEXITCODE -ne 0) { throw "web build failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
