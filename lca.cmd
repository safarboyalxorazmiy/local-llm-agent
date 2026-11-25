@echo off
"%~dp0venv\Scripts\python.exe" "%~dp0lca.py" %*
if errorlevel 1 pause
