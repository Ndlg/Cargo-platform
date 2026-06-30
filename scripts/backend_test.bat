@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0backend_test.ps1" %*
