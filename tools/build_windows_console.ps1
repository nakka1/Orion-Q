<# Build, validate and package the native Windows console. #>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipInstaller,
    [switch]$SkipDependencyInstall,
    [string]$ReleaseBaseDirectory = ""
)
$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location $repositoryRoot
if (-not $Python) { $Python = Join-Path $repositoryRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create build environment" }
}
$Python = (Resolve-Path -LiteralPath $Python).Path
if (-not $SkipDependencyInstall) {
    & $Python -m pip install -e ".[console,desktop,dev,satellite,lab]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
}
# PYTHONPATH=src can select this egg-info ahead of the venv's distribution.
# Refresh project metadata even when dependency installation is deliberately skipped.
& $Python -c "from setuptools import setup; setup()" egg_info
if ($LASTEXITCODE -ne 0) { throw "Project version metadata refresh failed" }
$productJson = & $Python -c "import json, runpy; product = runpy.run_path('src/quantum_autopilot/product.py'); print(json.dumps({'name': product['PRODUCT_NAME'], 'executable': product['EXECUTABLE_NAME'], 'directory': product['PACKAGE_DIRECTORY'], 'version': product['product_version']()}))"
if ($LASTEXITCODE -ne 0) { throw "Product identity and canonical version could not be read" }
$productMetadata = $productJson | ConvertFrom-Json
$version = $productMetadata.version
if (-not $version -or $version -eq "development") { throw "A declared release version is required" }
# Source and runtime-test storage may be on different volumes. Keep the full
# finite validation campaign on the selected build volume; no durability or
# timeout gate changes are needed when the checkout is on a slower disk.
$releaseBase = if ($ReleaseBaseDirectory) {
    [System.IO.Path]::GetFullPath($ReleaseBaseDirectory)
} else { Join-Path $repositoryRoot "release\windows" }
New-Item -ItemType Directory -Path $releaseBase -Force | Out-Null
$releaseRoot = Join-Path $releaseBase ($version + "-native-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $releaseRoot | Out-Null
$previousPythonPath = $env:PYTHONPATH
$previousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:PYTHONPATH = Join-Path $repositoryRoot "src"
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Python tools/generate_console_icon.py
    if ($LASTEXITCODE -ne 0) { throw "OrionQ desktop icon generation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-desktop-identity") --junitxml (Join-Path $releaseRoot "pytest-desktop-identity-results.xml") tests/test_console_branding.py tests/test_desktop_instance.py tests/test_frozen_desktop_behavior.py tests/test_console_publication_order.py
    if ($LASTEXITCODE -ne 0) { throw "Desktop icons, single-instance ownership and release gate validation failed" }
    & $Python tools/prepare_console_resources.py
    if ($LASTEXITCODE -ne 0) { throw "Runtime resource preparation failed" }
    & $Python tools/build_controller_evidence.py
    if ($LASTEXITCODE -ne 0) { throw "Reviewed controller catalog preparation failed" }
    & $Python tools/build_runtime_diagnostic_manifest.py
    if ($LASTEXITCODE -ne 0) { throw "Diagnostic provenance preparation failed" }
    $laboratoryTests = @(Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'tests') -Filter 'test_lab_*.py' | ForEach-Object { $_.FullName })
    if ($laboratoryTests.Count -eq 0) { throw "Laboratory regression corpus not found" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-laboratory") --junitxml (Join-Path $releaseRoot "pytest-laboratory-results.xml") @laboratoryTests tests/test_public_satellite_ephemeris.py
    if ($LASTEXITCODE -ne 0) { throw "Laboratory acquisition, recovery, independent QKD and public ephemeris validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-repeaters") --junitxml (Join-Path $releaseRoot "pytest-repeaters-results.xml") tests/test_institutional_repeaters.py tests/test_institutional_demo.py tests/test_network_engine_routing.py
    if ($LASTEXITCODE -ne 0) { throw "Paired-channel demo and repeater validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-modular") --junitxml (Join-Path $releaseRoot "pytest-modular-results.xml") tests/test_modular_quantum_protocols.py tests/test_console_modular_quantum.py tests/test_modular_runtime_validation.py tests/test_modular_observation_validation.py tests/test_communicators_geo.py
    if ($LASTEXITCODE -ne 0) { throw "Modular protocol, acquisition and UI validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-global-network") --junitxml (Join-Path $releaseRoot "pytest-global-network-results.xml") tests/test_global_network_operations.py tests/test_global_geo_constellation.py tests/test_satellite_isl_geo.py tests/test_console_global_globe.py
    if ($LASTEXITCODE -ne 0) { throw "Global demands, GEO/ISL admission and satellite interaction validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-communicator-forms") --junitxml (Join-Path $releaseRoot "pytest-communicator-forms-results.xml") tests/test_console_communicator_forms.py
    if ($LASTEXITCODE -ne 0) { throw "Communicator type-specific forms, field validation and durable save workflows failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-geo-modes") --junitxml (Join-Path $releaseRoot "pytest-geo-modes-results.xml") tests/test_console_geo_modes.py
    if ($LASTEXITCODE -ne 0) { throw "GEO satellites, input, state and exclusive renderer validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-satellite-v2") --junitxml (Join-Path $releaseRoot "pytest-satellite-v2-results.xml") tests/test_satellite_pipeline_v2.py tests/test_console_satellite_telemetry.py
    if ($LASTEXITCODE -ne 0) { throw "OGS ingress, satellite physics, finite-block processing and UI validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-satellite-mission") --junitxml (Join-Path $releaseRoot "pytest-satellite-mission-results.xml") tests/test_satellite_missions.py tests/test_satellite_radio.py tests/test_satellite_mission_ingress.py tests/test_satellite_gateway.py tests/test_satellite_gateway_cli.py tests/test_satellite_mission_engine.py tests/test_communicator_mission_registration.py tests/test_frozen_satellite_mission_validation.py
    if ($LASTEXITCODE -ne 0) { throw "Classical mission decode, KISS capture, authenticated ingress, durable gateway and release gate validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-startup") --junitxml (Join-Path $releaseRoot "pytest-startup-results.xml") tests/test_console_startup_stability.py tests/test_console_startup_acquisition_budget.py
    if ($LASTEXITCODE -ne 0) { throw "Startup stability regression validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-roadmap") --junitxml (Join-Path $releaseRoot "pytest-roadmap-results.xml") tests/test_controller_evidence.py tests/test_console_controller_comparison.py tests/test_console_polarisation_control.py tests/test_runtime_diagnostic.py tests/test_runtime_diagnostic_manifest.py tests/test_laboneq_emulation.py tests/test_platform_claims.py tests/test_roadmap_frozen_commands.py
    if ($LASTEXITCODE -ne 0) { throw "Roadmap evidence and diagnostics validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-console") --junitxml (Join-Path $releaseRoot "pytest-console-results.xml") tests/test_console_input_sources.py tests/test_orion_console.py tests/test_console_integration.py tests/test_desktop_app.py tests/test_physical_control_gate.py
    if ($LASTEXITCODE -ne 0) { throw "Console integration validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-display") --junitxml (Join-Path $releaseRoot "pytest-display-results.xml") tests/test_console_display_adaptation.py tests/test_console_usage_feedback.py tests/test_console_theme_transition.py
    if ($LASTEXITCODE -ne 0) { throw "Monitor geometry, DPI and interface adaptation validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-terminal") --junitxml (Join-Path $releaseRoot "pytest-terminal-results.xml") tests/test_console_terminal_workspace.py
    if ($LASTEXITCODE -ne 0) { throw "Terminal resize, close/reopen and shared screen layout validation failed" }
    # Keep independent Qt corpora in separate processes. Theme changes and queued
    # events from closed test windows otherwise make the subsequent reader corpus
    # much slower than the same cases in isolation; no coverage is skipped.
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-display-audit") --junitxml (Join-Path $releaseRoot "pytest-display-audit-results.xml") tests/test_console_command_reconciliation.py tests/test_console_policy_decisions.py tests/test_console_policy_watchdog.py tests/test_console_policy_binding_authority.py tests/test_policy_panel_benchmark.py
    if ($LASTEXITCODE -ne 0) { throw "Command receipts and decision audit display validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-display-readers") --junitxml (Join-Path $releaseRoot "pytest-display-readers-results.xml") tests/test_console_text_scrolling.py
    if ($LASTEXITCODE -ne 0) { throw "Text reader scrolling validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-display-live") --junitxml (Join-Path $releaseRoot "pytest-display-live-results.xml") tests/test_console_live_rendering.py tests/test_console_node_compositing.py tests/test_console_wave_rendering.py tests/test_console_wave_continuity.py tests/test_console_fluency_validation.py
    if ($LASTEXITCODE -ne 0) { throw "Live rendering and node compositing validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-window-chrome") --junitxml (Join-Path $releaseRoot "pytest-window-chrome-results.xml") tests/test_console_window_chrome.py tests/test_console_resize_regression.py
    if ($LASTEXITCODE -ne 0) { throw "Frameless window controls validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-institutional") --junitxml (Join-Path $releaseRoot "pytest-institutional-results.xml") tests/test_institutional_demo.py tests/test_console_institutional_demo.py
    if ($LASTEXITCODE -ne 0) { throw "Institutional geography, physical limits and confirmed ML control validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest") --junitxml (Join-Path $releaseRoot "pytest-results.xml") tests/test_console_screens.py tests/test_console_service_provider.py tests/test_console_provider_bootstrap.py tests/test_worker_runtime.py tests/test_console_temporal_campaign.py tests/test_temporal_campaign.py tests/test_temporal_history.py tests/test_windows_acceptance.py tests/test_temporal_history_search.py tests/test_campaign_attempts.py tests/test_attempt_export.py tests/test_attempt_file.py tests/test_attempt_filters.py tests/test_attempt_period.py tests/test_attempt_batch.py tests/test_attempt_archive.py tests/test_attempt_receipt.py tests/test_campaign_read_store.py tests/test_campaign_manifest_editor.py
    if ($LASTEXITCODE -ne 0) { throw "Console validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-live-network") --junitxml (Join-Path $releaseRoot "pytest-live-network-results.xml") tests/test_network_inventory.py tests/test_control_plane_topology.py tests/test_network_engine_sources.py tests/test_network_engine_models.py tests/test_network_engine_links.py tests/test_network_engine_routing.py tests/test_network_engine_calibration.py tests/test_network_engine_engine.py tests/test_network_engine_regressions.py tests/test_network_engine_service.py tests/test_network_baseline_worker.py tests/test_console_geodata.py tests/test_console_live_provider.py tests/test_console_shell.py tests/test_console_topology_live.py tests/test_console_node_window_layout.py tests/test_console_memory_lifecycle.py tests/test_console_screens_live.py tests/test_functional_topology.py tests/test_console_capabilities.py tests/test_console_polling.py tests/test_vendored_qnet.py tests/test_gmpy_shim_coexistence.py
    if ($LASTEXITCODE -ne 0) { throw "Live network (engine, GEO, demonstration) validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-controller-concurrency") --junitxml (Join-Path $releaseRoot "pytest-controller-concurrency-results.xml") tests/test_controller_concurrency_qualification.py tests/test_mixed_controller.py tests/test_mixed_controller_service.py
    if ($LASTEXITCODE -ne 0) { throw "Atomic reservation, physical resource authority, audit recovery and mixed controller validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-defect-corrections") --junitxml (Join-Path $releaseRoot "pytest-defect-corrections-results.xml") tests/test_console_provider_stop.py tests/test_prepared_source.py tests/test_pair_atomicity.py tests/test_orbital_resource_atomicity.py tests/test_routing_work_budget.py tests/test_keyrate_commissioning_cache.py tests/test_console_processing_labels.py tests/test_console_asset_compliance.py tests/test_sequence_control_execution_contract.py
    if ($LASTEXITCODE -ne 0) { throw "Current defect-correction gates failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-scientific-input") --junitxml (Join-Path $releaseRoot "pytest-scientific-input-results.xml") tests/test_scientific_runtime_validation.py
    if ($LASTEXITCODE -ne 0) { throw "Scientific input validation must remain effective in optimized interpreters" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-tick-layout") --junitxml (Join-Path $releaseRoot "pytest-tick-layout-results.xml") tests/test_console_tick_layout_budget.py tests/test_console_inspector_performance.py tests/test_console_wrapping_fields.py tests/test_console_floating_tick.py tests/test_console_node_window_lifecycle.py tests/test_console_node_capability_notifications.py tests/test_console_node_incremental.py
    if ($LASTEXITCODE -ne 0) { throw "Live detail layout, complete values and bounded tick presentation validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-visual-readability") --junitxml (Join-Path $releaseRoot "pytest-visual-readability-results.xml") tests/test_console_visual_readability.py
    if ($LASTEXITCODE -ne 0) { throw "Floating title visibility, satellite tab viewport and complete telemetry cell layout validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-channel-intake") --junitxml (Join-Path $releaseRoot "pytest-channel-intake-results.xml") tests/test_network_channel_io.py tests/test_console_channel_configuration.py
    if ($LASTEXITCODE -ne 0) { throw "Paired channel configuration, acquisition replay and common intake validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-commands") --junitxml (Join-Path $releaseRoot "pytest-commands-results.xml") tests/test_policy_preparation.py tests/test_policy_approval.py tests/test_policy_activation.py tests/test_policy_recovery.py tests/test_console_policy_review.py tests/test_console_policy_approval.py tests/test_console_policy_activation.py tests/test_console_policy_recovery.py tests/test_policy_decisions.py tests/test_console_policy_decisions.py tests/test_policy_compatibility.py tests/test_console_policy_compatibility.py tests/test_policy_context.py tests/test_console_policy_context.py tests/test_console_commands.py tests/test_command_reconciliation.py tests/test_console_command_reconciliation.py tests/test_console_inventory.py tests/test_console_link_editor.py
    if ($LASTEXITCODE -ne 0) { throw "Command lifecycle validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-optional") --junitxml (Join-Path $releaseRoot "pytest-optional-results.xml") tests/test_optional_models_acceptance.py tests/test_optional_sync_shadow.py tests/test_optional_sync_session.py tests/test_console_sync_session.py tests/test_optional_keyrate_session.py tests/test_console_keyrate_session.py tests/test_optional_compensation_session.py tests/test_console_compensation_session.py tests/test_optional_routing_session.py tests/test_console_routing_session.py tests/test_optional_allocation_session.py tests/test_console_allocation_session.py tests/test_console_model_catalog.py tests/test_model_archive_verification.py tests/test_detector_safe.py tests/test_detector_session.py
    if ($LASTEXITCODE -ne 0) { throw "Optional model validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-sync-shadow") --junitxml (Join-Path $releaseRoot "pytest-sync-shadow-results.xml") tests/test_console_sync_shadow.py tests/test_sync_temporal.py tests/test_sync_campaign.py
    if ($LASTEXITCODE -ne 0) { throw "Continuous synchronization shadow validation failed" }
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $releaseRoot "pytest-compensation-shadow") --junitxml (Join-Path $releaseRoot "pytest-compensation-shadow-results.xml") tests/test_optional_compensation_shadow.py tests/test_optional_allocation_shadow.py tests/test_optional_routing_shadow.py tests/test_optional_keyrate_shadow.py tests/test_console_keyrate_shadow.py tests/test_console_routing_shadow.py tests/test_console_allocation_shadow.py tests/test_console_compensation_shadow.py tests/test_compensation_temporal.py tests/test_compensation_campaign.py
    if ($LASTEXITCODE -ne 0) { throw "Polarisation shadow bridge validation failed" }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:QT_QPA_PLATFORM = $previousQtPlatform
}
$previousFreezePythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $repositoryRoot "src"
    & $Python -m PyInstaller packaging/console.spec --noconfirm --distpath $releaseRoot --workpath (Join-Path $releaseRoot "build")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$releaseProductRoot = Join-Path $releaseRoot $productMetadata.directory
$exe = Join-Path $releaseProductRoot $productMetadata.executable
$brandingRoot = Join-Path $releaseRoot "branding-validation"
$brandingProcess = Start-Process -FilePath $exe -ArgumentList @("--branding-test", ('"' + $brandingRoot + '"')) -WindowStyle Hidden -PassThru
if (-not $brandingProcess.WaitForExit(180000)) {
    Stop-Process -Id $brandingProcess.Id -ErrorAction SilentlyContinue
    throw "Frozen branding and version validation timed out; package not approved"
}
if ($brandingProcess.ExitCode -ne 0) { throw "Frozen branding and version validation failed; package not approved" }
$brandingReport = Join-Path $brandingRoot "branding-report.json"
if (-not (Test-Path -LiteralPath $brandingReport) -or -not (Get-Content -Raw -LiteralPath $brandingReport | ConvertFrom-Json).passed) {
    throw "Frozen branding evidence is missing or was not approved"
}
& $Python tools/validate_frozen_desktop_behavior.py --exe $exe --output-dir (Join-Path $releaseRoot "desktop-behavior-validation")
if ($LASTEXITCODE -ne 0) { throw "Frozen Windows icon and single-instance validation failed; package not approved" }
& $Python tools/validate_frozen_roadmap.py --exe $exe --output-dir (Join-Path $releaseRoot "roadmap-validation")
if ($LASTEXITCODE -ne 0) { throw "Frozen roadmap validation failed" }
$optionalRoot = Join-Path $releaseRoot "optional-models-validation"
$optionalProcess = Start-Process -FilePath $exe -ArgumentList @("--optional-model-test", "--output-dir", ('"' + $optionalRoot + '"')) -WindowStyle Hidden -PassThru
if (-not $optionalProcess.WaitForExit(120000)) {
    Stop-Process -Id $optionalProcess.Id -ErrorAction SilentlyContinue
    throw "Optional model diagnostic timed out"
}
if ($optionalProcess.ExitCode -ne 0) { throw "Optional model diagnostic failed" }
& $Python tools/validate_frozen_temporal_campaign.py --exe $exe --output-dir (Join-Path $releaseRoot "campaign-validation")
if ($LASTEXITCODE -ne 0) { throw "Frozen temporal campaign validation failed; package not approved" }
$report = Join-Path $releaseRoot "self-test.json"
$process = Start-Process -FilePath $exe -ArgumentList @("--self-test", ('"' + $report + '"'), "--state-root", ('"' + (Join-Path $releaseRoot "validation-state") + '"')) -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw "Frozen self-test failed" }
if (-not (Get-Content -Raw -LiteralPath $report | ConvertFrom-Json).passed) { throw "Invalid frozen self-test" }
$scientificReport = Join-Path $releaseRoot "scientific-input-validation.json"
$scientificProcess = Start-Process -FilePath $exe -ArgumentList @("--scientific-input-test", ('"' + $scientificReport + '"')) -WindowStyle Hidden -PassThru
if (-not $scientificProcess.WaitForExit(180000)) { Stop-Process -Id $scientificProcess.Id -ErrorAction SilentlyContinue; throw "Frozen scientific input validation timed out" }
if ($scientificProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $scientificReport)) { throw "Frozen scientific input validation failed" }
$scientificEvidence = Get-Content -Raw -LiteralPath $scientificReport | ConvertFrom-Json
if (-not $scientificEvidence.passed -or -not $scientificEvidence.frozen) { throw "Invalid frozen scientific input evidence" }
$laboratoryRoot = Join-Path $releaseRoot "laboratory-validation"
$laboratoryProcess = Start-Process -FilePath $exe -ArgumentList @("--lab-test", ('"' + $laboratoryRoot + '"')) -WindowStyle Hidden -PassThru
if (-not $laboratoryProcess.WaitForExit(300000)) { $laboratoryProcess.Kill(); throw "Frozen laboratory validation timed out" }
if ($laboratoryProcess.ExitCode -ne 0) { throw "Frozen laboratory validation failed" }
$laboratoryReport = Get-Content -Raw -LiteralPath (Join-Path $laboratoryRoot "report.json") | ConvertFrom-Json
if (-not $laboratoryReport.passed -or -not $laboratoryReport.frozen -or -not $laboratoryReport.full_campaign) { throw "Invalid frozen laboratory evidence" }
$satelliteRoot = Join-Path $releaseRoot "satellite-validation"
$satelliteProcess = Start-Process -FilePath $exe -ArgumentList @("--satellite-test", ('"' + $satelliteRoot + '"'), "--seed", "42") -WindowStyle Hidden -PassThru
if (-not $satelliteProcess.WaitForExit(120000)) { Stop-Process -Id $satelliteProcess.Id; throw "Frozen OGS validation timed out" }
if ($satelliteProcess.ExitCode -ne 0 -or -not (Get-Content -Raw -LiteralPath (Join-Path $satelliteRoot "report.json") | ConvertFrom-Json).passed) { throw "Frozen satellite/OGS v2 validation failed" }
& $Python tools/validate_frozen_satellite_mission.py --exe $exe --output-dir (Join-Path $releaseRoot "satellite-mission-validation")
if ($LASTEXITCODE -ne 0) { throw "Frozen classical satellite mission reception validation failed; package not approved" }
$globalRoot = Join-Path $releaseRoot "global-validation"
$globalProcess = Start-Process -FilePath $exe -ArgumentList @("--global-network-test", ('"' + $globalRoot + '"')) -WindowStyle Hidden -PassThru
if (-not $globalProcess.WaitForExit(600000)) { Stop-Process -Id $globalProcess.Id; throw "Frozen global-network validation timed out" }
if ($globalProcess.ExitCode -ne 0 -or -not (Get-Content -Raw -LiteralPath (Join-Path $globalRoot "report.json") | ConvertFrom-Json).passed) { throw "Frozen global-network validation failed" }
$stressRoot = Join-Path $releaseRoot "stress-validation"
$stressProcess = Start-Process -FilePath $exe -ArgumentList @("--stress-test", ('"' + $stressRoot + '"'), "--stress-rounds", "20", "--seed", "1337") -WindowStyle Hidden -PassThru
if (-not $stressProcess.WaitForExit(180000)) {
    Stop-Process -Id $stressProcess.Id -ErrorAction SilentlyContinue
    throw "Frozen controller stress timed out; package not approved"
}
if ($stressProcess.ExitCode -ne 0) { throw "Frozen controller stress failed; inspect $stressRoot" }
$stressReports = @(Get-ChildItem -LiteralPath $stressRoot -Recurse -Filter report.json)
if ($stressReports.Count -ne 1 -or -not (Get-Content -Raw -LiteralPath $stressReports[0].FullName | ConvertFrom-Json).passed) { throw "Invalid frozen stress report" }
& $Python tools/validate_frozen_startup.py --exe $exe --output-dir (Join-Path $releaseRoot "startup-validation")
if ($LASTEXITCODE -ne 0) { throw "Ordinary native startup validation failed; package not approved" }
& $Python tools/package_console_portable.py --release-root $releaseRoot --worker-report (Join-Path $releaseRoot "campaign-validation\report.json")
if ($LASTEXITCODE -ne 0) { throw "Portable packaging or integrity verification failed" }
$zipPath = (Get-Content -LiteralPath (Join-Path $releaseRoot "portable-package.json") -Raw | ConvertFrom-Json).archive
$acceptanceRoot = Join-Path $releaseRoot 'native-acceptance'
$acceptanceProcess = Start-Process -FilePath $exe -ArgumentList @('--acceptance-test', '--timeout-seconds', '3600', '--output-dir', ('"' + $acceptanceRoot + '"')) -WindowStyle Hidden -PassThru
# Self-test and stress each have the full finite timeout, plus report overhead.
if (-not $acceptanceProcess.WaitForExit((2 * 3600 + 120) * 1000)) {
    Stop-Process -Id $acceptanceProcess.Id -ErrorAction SilentlyContinue
    throw 'Native acceptance timed out; do not distribute the generated ZIP'
}
if ($acceptanceProcess.ExitCode -ne 0) { throw 'Native acceptance failed; do not distribute the generated ZIP' }
$acceptanceReport = Get-Content -LiteralPath (Join-Path $acceptanceRoot 'acceptance-report.json') -Raw | ConvertFrom-Json
if ($acceptanceReport.automatic_passed -isnot [bool] -or $acceptanceReport.automatic_passed -ne $true) {
    throw 'Native acceptance was not approved; do not distribute the generated ZIP'
}
if (-not $SkipInstaller) {
    $compiler = Get-Command iscc -ErrorAction SilentlyContinue
    $compilerPath = if ($compiler) { $compiler.Source } else { "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path -LiteralPath $compilerPath)) {
        throw "Portable package ready at $zipPath. Inno Setup is required to produce the installer; use -SkipInstaller for portable delivery."
    }
    & $compilerPath "/DAppVersion=$version" "/DAppName=$($productMetadata.name)" "/DAppExe=$($productMetadata.executable)" "/DSourceRoot=$releaseProductRoot" "/O$releaseRoot" packaging/installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed" }
}
Write-Output $zipPath
}
finally { $env:PYTHONPATH = $previousFreezePythonPath }
