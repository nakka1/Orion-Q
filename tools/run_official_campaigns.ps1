<#
.SYNOPSIS
Runs the frozen ORION-Q campaigns on this machine with the pinned ZIPs and compares
every result with the published cloud summaries (docs/results).

.DESCRIPTION
Each campaign writes into its own subdirectory of -Output. A campaign whose
summary.json already exists is skipped, so the script can be interrupted and run
again. At the end, tools/compare_campaign_summaries.py compares every number with
the cloud run (relative tolerance 1e-9) and writes comparison.json. Nothing here
activates a policy or acts on hardware.

Approximate durations on a 4-core laptop: detector 5 min, causal detector
15 min, QKD 25 min, routing
20 min, routing in SeQUeNCe 20 min, polarisation 15 min per point, quantisation 60 min, key blocking 20 min, allocation 70 min,
actuator 2-3 h (use -Workers 4). The RTL check needs iverilog, vvp and yosys on
PATH and is skipped when they are missing. Routing in SeQUeNCe needs -AuthorZip
(the author's repository archive, Qnet-Network ML.zip, for the phase-2 weights of the
gate) and SeQUeNCe (SEQUENCE_SRC pointing at a checkout of commit 3133c66d, as in the
cloud run), and is skipped otherwise; its tree-classifier part retrains the
author's detector and needs scikit-learn 1.8.0 (the author's version), otherwise it
is skipped inside the tool and the comparison reports the rtl campaign as different.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\run_official_campaigns.ps1 -QnetZip F:\qnet_network.zip -QkdZip "$env:USERPROFILE\Downloads\modelo_qkd_ml.zip" -AuthorZip "$env:USERPROFILE\Downloads\Qnet-Network ML.zip"

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\run_official_campaigns.ps1 -QnetZip F:\qnet_network.zip -QkdZip modelo_qkd_ml.zip -Only detector,qkd
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$QnetZip,
    [Parameter(Mandatory = $true)][string]$QkdZip,
    [string]$AuthorZip = "",
    [string]$Python = "",
    [string]$Output = "",
    [string[]]$Only = @(),
    [int]$Workers = 2
)
$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location $repositoryRoot
if (-not $Python) {
    $candidate = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $candidate) { $candidate } else { "python" }
}
$QnetZip = (Resolve-Path -LiteralPath $QnetZip).Path
$QkdZip = (Resolve-Path -LiteralPath $QkdZip).Path
if ($AuthorZip) { $AuthorZip = (Resolve-Path -LiteralPath $AuthorZip).Path }
if (-not $Output) { $Output = Join-Path $repositoryRoot ("outputs\official-" + (Get-Date -Format "yyyyMMdd-HHmm")) }
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$env:PYTHONPATH = Join-Path $repositoryRoot "src"

$campaigns = [ordered]@{
    "detector"                     = @("tools/run_detector_evaluation.py", "--qkd-zip", $QkdZip)
    "detector-causal"              = @("tools/run_detector_causal.py", "--qkd-zip", $QkdZip,
                                       "--workers", [string][Math]::Min($Workers, 2))
    "qkd"                          = @("tools/run_qkd_closed_loop.py", "--qkd-zip", $QkdZip)
    "polarisation-calibrado"       = @("tools/run_polarisation_closed_loop.py", "--qnet-zip", $QnetZip)
    "polarisation-desenvolvimento" = @("tools/run_polarisation_closed_loop.py", "--qnet-zip", $QnetZip,
                                       "--protocol", "configs/optional_models/polarisation_closed_loop_dev_v1.json")
    "polarisation-demo"            = @("tools/polarisation_control_demo.py", "--qnet-zip", $QnetZip, "--evidence",
                                       (Join-Path (Join-Path $Output "polarisation-calibrado") "summary.json"),
                                       "--operator", "official-run", "--confirm")
    "routing"                      = @("tools/run_routing_closed_loop.py", "--qnet-zip", $QnetZip)
    "routing-sequence"             = @("tools/run_routing_sequence.py", "--qnet-zip", $QnetZip, "--author-zip",
                                       $AuthorZip, "--workers", [string][Math]::Min($Workers, 2))
    "polarisation-quantisation"    = @("tools/run_polarisation_quantisation.py", "--qnet-zip", $QnetZip,
                                       "--workers", [string][Math]::Min($Workers, 2))
    "polarisation-blocking"        = @("tools/run_polarisation_blocking.py", "--qnet-zip", $QnetZip,
                                       "--workers", [string][Math]::Min($Workers, 3))
    "allocation"                   = @("tools/run_allocation_closed_loop.py", "--qnet-zip", $QnetZip)
    "polarisation-actuator"        = @("tools/run_polarisation_actuator.py", "--qnet-zip", $QnetZip,
                                       "--workers", [string]$Workers)
    "rtl"                          = @("tools/verify_author_rtl.py", "--qkd-zip", $QkdZip)
}
# "-Only a,b" arrives as one string under powershell -File; accept both forms.
$Only = @($Only | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$selected = if ($Only.Count) { $Only } else { @($campaigns.Keys) }
foreach ($name in $selected) {
    if (-not $campaigns.Contains($name)) { throw "Unknown campaign '$name'. Choose from: $($campaigns.Keys -join ', ')" }
    $target = Join-Path $Output $name
    if (Test-Path -LiteralPath (Join-Path $target "summary.json")) {
        Write-Output "[$name] already complete, skipped"
        continue
    }
    if ($name -eq "rtl" -and -not ((Get-Command iverilog -ErrorAction SilentlyContinue) -and (Get-Command yosys -ErrorAction SilentlyContinue))) {
        Write-Output "[$name] iverilog/yosys not on PATH, skipped"
        continue
    }
    if ($name -eq "routing-sequence") {
        if (-not $AuthorZip) {
            Write-Output "[$name] -AuthorZip (Qnet-Network ML.zip) not given, skipped"
            continue
        }
        & $Python -c "from qnet_network.compat import bootstrap; bootstrap()" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Output "[$name] SeQUeNCe not importable (set SEQUENCE_SRC to a checkout), skipped"
            continue
        }
    }
    if (Test-Path -LiteralPath $target) {
        $stale = Join-Path $Output ("$name-incomplete-" + (Get-Date -Format "HHmmss"))
        Move-Item -LiteralPath $target -Destination $stale
        Write-Output "[$name] previous incomplete run moved to $stale"
    }
    $arguments = @($campaigns[$name]) + @("--output", $target)
    Write-Output "[$name] started $(Get-Date -Format 'HH:mm')"
    # Progress goes to stderr in some tools; Windows PowerShell must not treat it as an error.
    $ErrorActionPreference = "Continue"
    # UTF-8 log (Tee-Object in Windows PowerShell 5.1 writes UTF-16).
    $log = Join-Path $Output "$name.log"
    & $Python @arguments 2>&1 | ForEach-Object { $line = "$_"; Add-Content -LiteralPath $log -Value $line -Encoding UTF8; $line }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -ne 0) { throw "[$name] failed; see $(Join-Path $Output "$name.log")" }
}
$completed = @($selected | Where-Object { Test-Path -LiteralPath (Join-Path (Join-Path $Output $_) "summary.json") })
if (-not $completed.Count) { throw "No campaign completed" }
& $Python tools/compare_campaign_summaries.py --official $Output --only @completed
if ($LASTEXITCODE -ne 0) { throw "Official results differ from the cloud summaries; see $(Join-Path $Output 'comparison.json')" }
Write-Output "All selected campaigns match the published results: $Output"
