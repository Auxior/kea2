@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "KEA2_LAUNCH_EXIT=1"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
    if not errorlevel 1 (
        call :run ".venv\Scripts\python.exe"
        goto :finish
    )
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    call :run py -3
    goto :finish
)
for %%V in (3.14 3.13 3.12 3.11) do (
    py -%%V -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
    if not errorlevel 1 (
        call :run py -%%V
        goto :finish
    )
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    call :run python
    goto :finish
)
echo Python 3.11+ was not found. Install Python from:
echo https://www.python.org/downloads/windows/
echo Enable the Python launcher or add Python to PATH, then try again.
goto :finish

:run
%* -u launch_workbench.py
set "KEA2_LAUNCH_EXIT=%errorlevel%"
exit /b %KEA2_LAUNCH_EXIT%

:finish
if not "%KEA2_LAUNCH_EXIT%"=="0" pause
popd
exit /b %KEA2_LAUNCH_EXIT%
