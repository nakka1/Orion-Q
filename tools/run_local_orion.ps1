param(
    [ValidateSet('console', 'lab')][string]$Mode = 'console',
    [string]$Python,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    foreach ($relative in @('.venv\Scripts\python.exe', '.audit-winvenv\Scripts\python.exe')) {
        $candidate = Join-Path $repositoryRoot $relative
        if (Test-Path -LiteralPath $candidate) { $Python = $candidate; break }
    }
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$sourceRoot = Join-Path $repositoryRoot 'src'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $sourceRoot
    if ($previousPythonPath) { $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath }
    $module = if ($Mode -eq 'lab') { 'quantum_autopilot.lab.cli' } else { 'quantum_autopilot.desktop' }
    & $Python -m $module @Arguments
    $resultCode = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $resultCode
