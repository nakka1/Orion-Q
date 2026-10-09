@echo off
rem Compatibility entry point for automation that uses the former product name.
call "%~dp0VALIDATE-OrionQ.cmd"
exit /b %ERRORLEVEL%
