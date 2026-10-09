@echo off
setlocal
title OrionQ - Local acceptance
set "orion_executable=%~dp0OrionQ.exe"
if not exist "%orion_executable%" set "orion_executable=%~dp0ORION-Q.exe"
if not exist "%orion_executable%" (
    echo OrionQ executable was not found. Extract the complete portable package.
    pause
    exit /b 2
)
set "orion_report=%TEMP%\ORION-Q-acceptance-%RANDOM%-%RANDOM%"
echo OrionQ local software checks. No physical device will be controlled.
echo The current operator journal will not be used.
echo Please wait. Reports: %orion_report%
start /wait "" "%orion_executable%" --acceptance-test --timeout-seconds 3600 --output-dir "%orion_report%"
set "orion_code=%ERRORLEVEL%"
echo.
echo Exit code: %orion_code% (0 = automatic checks finished, not full acceptance)
echo Report: %orion_report%\acceptance-report.json
echo Manual review on another computer is still required. Read docs\orion_q_windows_acceptance.md.
echo If no report exists, check Windows protection messages and the diagnostics guide.
pause
exit /b %orion_code%
