@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
rem OneDrive uploads every scratch file a comparison writes, which makes large comparisons very slow.
rem When this folder is synced, keep comparison data in the local, never-synced profile folder instead.
set "DATA_ARGS="
echo "%~dp0" | find /i "OneDrive" >nul
if not errorlevel 1 set DATA_ARGS=--data-dir "%LOCALAPPDATA%\DataCompare\data"
if defined DATA_ARGS echo This folder is synced by OneDrive; comparison data is kept in %LOCALAPPDATA%\DataCompare\data
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0server.py" --open %DATA_ARGS%
  goto done
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9) or sys.maxsize <= 2**32)" >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0server.py" --open %DATA_ARGS%
goto done

:try_python
python -c "import sys; sys.exit(sys.version_info < (3, 9) or sys.maxsize <= 2**32)" >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0server.py" --open %DATA_ARGS%
goto done

:missing_python
echo Python 3.9 or newer is required. Install 64-bit Python and run this file again.
pause
exit /b 1

:done
if errorlevel 1 pause
endlocal
