@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    uv sync --locked
    if errorlevel 1 goto environment_error
)
if not exist ".venv\Scripts\pythonw.exe" (
    goto environment_error
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m activity_check
exit /b 0

:environment_error
echo Unable to prepare Python environment. Install Python 3.13, uv and Microsoft Edge.
pause
exit /b 1
