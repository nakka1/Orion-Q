param(
    [string]$Python = ".\.audit-winvenv\Scripts\python.exe",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Write-Warning "Legacy 5.34 web-only builder retained for historical reproduction. Current OrionQ releases use tools/build_windows_console.ps1; OrionQ.exe --ui web selects the existing web entry point."
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$pythonPath = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot $Python)).Path
$dashboardRoot = Join-Path $repositoryRoot "dashboard"
$staticRoot = Join-Path $dashboardRoot "dist\client"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$releaseRoot = Join-Path $repositoryRoot "release\windows\5.34.0-$stamp"
$workRoot = Join-Path $repositoryRoot ".audit-pyinstaller-v534-$stamp"
$specPath = Join-Path $repositoryRoot "packaging\NISQPredictiveTwin.spec"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python executable not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
    throw "PyInstaller specification not found: $specPath"
}

$previousStaticExport = $env:QUANTUM_TWIN_STATIC_EXPORT
$previousApiUrl = $env:NEXT_PUBLIC_ASSURANCE_API_URL
try {
    $env:QUANTUM_TWIN_STATIC_EXPORT = "1"
    $env:NEXT_PUBLIC_ASSURANCE_API_URL = "/api/v1"
    Push-Location $dashboardRoot
    try {
        & pnpm run build
        if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed." }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:QUANTUM_TWIN_STATIC_EXPORT = $previousStaticExport
    $env:NEXT_PUBLIC_ASSURANCE_API_URL = $previousApiUrl
}

if (-not (Test-Path -LiteralPath (Join-Path $staticRoot "index.html") -PathType Leaf)) {
    throw "Static dashboard was not generated at $staticRoot"
}

if (-not $SkipTests) {
    Push-Location $repositoryRoot
    try {
        $env:PYTHONPATH = Join-Path $repositoryRoot "src"
        & $pythonPath -m pytest -q tests/test_desktop_app.py tests/test_qkd_dashboard_api.py
        if ($LASTEXITCODE -ne 0) { throw "Desktop/API tests failed." }
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
Push-Location $repositoryRoot
try {
    & $pythonPath -m PyInstaller `
        --noconfirm `
        --distpath $releaseRoot `
        --workpath $workRoot `
        $specPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}
finally {
    Pop-Location
}

$productRoot = Join-Path $releaseRoot "NISQPredictiveTwin"
$executable = Join-Path $productRoot "NISQPredictiveTwin.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Expected executable was not generated: $executable"
}

Copy-Item -LiteralPath (Join-Path $repositoryRoot "packaging\README-WINDOWS.txt") -Destination $productRoot
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    (Join-Path $productRoot "SHA256SUMS.txt"),
    "$hash  NISQPredictiveTwin.exe`r`n",
    [System.Text.UTF8Encoding]::new($false)
)
$zipPath = Join-Path $releaseRoot "NISQPredictiveTwin-5.34.0-windows-x64.zip"
Compress-Archive -LiteralPath $productRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    "$zipPath.sha256",
    "$zipHash  $(Split-Path -Leaf $zipPath)`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Executable: $executable"
Write-Host "SHA-256:   $hash"
Write-Host "Package:   $zipPath"
Write-Host "ZIP SHA:   $zipHash"
