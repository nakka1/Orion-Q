<# Snapshot source plus available scientific outputs, without build environments. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$zipPath = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $zipPath) { throw "Refusing to overwrite: $zipPath" }
$files = [Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
Push-Location $repositoryRoot
try {
    $sourceFiles = & git -c "safe.directory=$repositoryRoot" -c core.quotepath=false ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) { throw 'Cannot enumerate repository sources' }
    foreach ($relative in $sourceFiles) {
        if ($relative -match '(^|/)(node_modules|__pycache__|release|build|dist|\.git|\.audit-[^/]*)(/|$)') { continue }
        if (Test-Path -LiteralPath $relative -PathType Leaf) { [void]$files.Add($relative.Replace('\','/')) }
    }
    # Downloaded raw data, test scratch directories and caches are not a source release.
    foreach ($item in Get-ChildItem -LiteralPath outputs) {
        if ($item.Name -like 'pytest-*' -or $item.Name.StartsWith('.')) { continue }
        $artifacts = if ($item.PSIsContainer) { Get-ChildItem -LiteralPath $item.FullName -Recurse -File } else { @($item) }
        foreach ($artifact in $artifacts) {
            $relative = $artifact.FullName.Substring($repositoryRoot.Length + 1).Replace('\','/')
            if ($relative -notmatch '(^|/)(__pycache__|\.pytest_cache)(/|$)') { [void]$files.Add($relative) }
        }
    }
    if (Test-Path -LiteralPath dashboard/dist) {
        foreach ($artifact in Get-ChildItem -LiteralPath dashboard/dist -Recurse -File) {
            [void]$files.Add($artifact.FullName.Substring($repositoryRoot.Length + 1).Replace('\','/'))
        }
    }
    foreach ($required in @('pyproject.toml', 'src/quantum_autopilot/console/app.py', 'runtime_assets/native/v1/manifest.json', 'tools/build_windows_console.ps1')) {
        if (-not $files.Contains($required)) { throw "Missing source file: $required" }
    }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($zipPath)) | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($relative in $files) {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, (Join-Path $repositoryRoot $relative), "NISQ-Predictive-Twin/$relative", [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $archive.Dispose() }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($zipPath))" | Set-Content -LiteralPath ($zipPath + '.sha256') -Encoding utf8
    [pscustomobject]@{ Path=$zipPath; Files=$files.Count; SHA256=$hash; Bytes=(Get-Item -LiteralPath $zipPath).Length }
} finally { Pop-Location }
