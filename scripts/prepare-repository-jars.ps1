[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("esb", "imc", "agent")]
    [string]$Project,

    [Parameter(Mandatory)]
    [string]$SourceRoot,

    [string]$CacheRoot = "E:\Data\LocalKnowledgePortal\cache\repository-analysis\decompiled",
    [string]$Java = "C:\Dev\Java\jdk17\bin\java.exe",
    [string]$Vineflower = "C:\Dev\Tools\Vineflower\1.12.0\vineflower-1.12.0.jar"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$resolvedCache = [System.IO.Path]::GetFullPath($CacheRoot)
$blobRoot = Join-Path $resolvedCache "blobs"
$viewRoot = Join-Path $resolvedCache (Join-Path "projects" $Project)
$manifestPath = Join-Path $viewRoot "jar-evidence-manifest.json"

foreach ($required in @($Java, $Vineflower)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required tool is missing: $required"
    }
}
New-Item -ItemType Directory -Path $blobRoot, $viewRoot -Force | Out-Null

function Get-JarMetadata {
    param([System.IO.FileInfo]$Jar)

    $zip = [System.IO.Compression.ZipFile]::OpenRead($Jar.FullName)
    try {
        $classes = @(
            $zip.Entries |
                Where-Object {
                    $_.FullName.EndsWith(".class", [System.StringComparison]::OrdinalIgnoreCase) -and
                    -not $_.FullName.StartsWith("META-INF/", [System.StringComparison]::OrdinalIgnoreCase)
                }
        )
        $internalClasses = @(
            $classes |
                Where-Object { $_.FullName -match "^(com/indigo|com/metanet|kr/co/|indigo/)" }
        )
        [pscustomobject]@{
            IsInternal = $internalClasses.Count -gt 0
            ClassCount = $classes.Count
            InternalClassCount = $internalClasses.Count
            Packages = @(
                $internalClasses |
                    ForEach-Object {
                        $parts = $_.FullName.Split("/")
                        if ($parts.Count -gt 1) {
                            ($parts[0..([Math]::Min(3, $parts.Count - 2))] -join ".")
                        }
                    } |
                    Where-Object { $_ } |
                    Sort-Object -Unique |
                    Select-Object -First 100
            )
        }
    }
    finally {
        $zip.Dispose()
    }
}

function Ensure-DecompiledBlob {
    param(
        [System.IO.FileInfo]$Jar,
        [string]$Hash
    )

    $destination = Join-Path $blobRoot $Hash
    $marker = Join-Path $destination ".vineflower-1.12.0.complete"
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        return $destination
    }

    $temporary = Join-Path $blobRoot (".tmp-{0}-{1}" -f $Hash, [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    & $Java -jar $Vineflower --silent $Jar.FullName $temporary
    if ($LASTEXITCODE -ne 0) {
        throw "Vineflower failed for $($Jar.FullName) with exit code $LASTEXITCODE"
    }
    $javaCount = @(Get-ChildItem -LiteralPath $temporary -Recurse -File -Filter *.java).Count
    if ($javaCount -eq 0) {
        throw "Vineflower produced no Java source for $($Jar.FullName)"
    }
    New-Item -ItemType File -Path (Join-Path $temporary ".vineflower-1.12.0.complete") |
        Out-Null
    if (Test-Path -LiteralPath $destination) {
        throw "Incomplete cache destination already exists: $destination"
    }
    Move-Item -LiteralPath $temporary -Destination $destination
    return $destination
}

$records = [System.Collections.Generic.List[object]]::new()
$seen = @{}
foreach ($jar in Get-ChildItem -LiteralPath $resolvedSource -Recurse -File -Filter *.jar) {
    $metadata = Get-JarMetadata -Jar $jar
    if (-not $metadata.IsInternal) {
        continue
    }
    $hash = (Get-FileHash -LiteralPath $jar.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($seen.ContainsKey($hash)) {
        $seen[$hash].duplicate_paths += $jar.FullName.Substring($resolvedSource.Length + 1)
        continue
    }

    $blob = Ensure-DecompiledBlob -Jar $jar -Hash $hash
    $safeName = [System.IO.Path]::GetFileNameWithoutExtension($jar.Name) -replace "[^A-Za-z0-9._-]", "_"
    $viewName = "{0}__{1}" -f $safeName, $hash.Substring(0, 12)
    $view = Join-Path $viewRoot $viewName
    New-Item -ItemType Directory -Path $view -Force | Out-Null
    foreach ($source in Get-ChildItem -LiteralPath $blob -Recurse -File -Filter *.java) {
        $relative = $source.FullName.Substring($blob.Length + 1)
        $link = Join-Path $view $relative
        $linkDirectory = Split-Path -Parent $link
        New-Item -ItemType Directory -Path $linkDirectory -Force | Out-Null
        if (-not (Test-Path -LiteralPath $link)) {
            New-Item -ItemType HardLink -Path $link -Target $source.FullName | Out-Null
        }
    }

    $record = [pscustomobject]@{
        project = $Project
        source_jar = $jar.FullName.Substring($resolvedSource.Length + 1)
        sha256 = $hash
        size_bytes = $jar.Length
        class_count = $metadata.ClassCount
        internal_class_count = $metadata.InternalClassCount
        internal_packages = $metadata.Packages
        evidence_directory = $viewName
        duplicate_paths = @()
        decompiler = "Vineflower 1.12.0"
    }
    $seen[$hash] = $record
    $records.Add($record)
}

$payload = [ordered]@{
    project = $Project
    source_root = $resolvedSource
    generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    decompiler = [ordered]@{
        name = "Vineflower"
        version = "1.12.0"
        sha256 = "1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d"
    }
    jar_count = $records.Count
    jars = $records
}
$temporaryManifest = "$manifestPath.tmp-$([guid]::NewGuid().ToString('N'))"
[System.IO.File]::WriteAllText(
    $temporaryManifest,
    ($payload | ConvertTo-Json -Depth 8),
    [System.Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $temporaryManifest -Destination $manifestPath -Force

[pscustomobject]@{
    project = $Project
    internal_unique_jars = $records.Count
    decompiled_java_files = @(
        Get-ChildItem -LiteralPath $viewRoot -Recurse -File -Filter *.java
    ).Count
    manifest = $manifestPath
} | ConvertTo-Json -Compress
