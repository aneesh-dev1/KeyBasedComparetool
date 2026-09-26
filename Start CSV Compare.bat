@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10) or sys.maxsize <= 2**32)" >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0server.py" --open
goto done

:try_python
python -c "import sys; sys.exit(sys.version_info < (3, 10) or sys.maxsize <= 2**32)" >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0server.py" --open
goto done

:missing_python
echo Python 3.10 or newer is required. Install 64-bit Python and run this file again.
pause
exit /b 1

:done
if errorlevel 1 pause
endlocal
