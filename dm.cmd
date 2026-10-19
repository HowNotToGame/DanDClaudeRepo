@echo off
rem RoboDM command line for Windows cmd and PowerShell:  dm <command> [...]   (see dm --help)
rem Uses the first Python 3.9+ that actually runs. Each candidate is test-run, because "python3" is often
rem the Microsoft Store placeholder: it's on PATH but only prints "Python was not found".
rem Force one with ROBODM_PYTHON, e.g.  set ROBODM_PYTHON=C:\Python312\python.exe
setlocal
set "PYTHONIOENCODING=utf-8"
set "RDM_CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
set "RDM_BOOT=import sys; sys.path.insert(0, sys.argv.pop(1)); from robodm.entry import main; sys.exit(main())"

if not defined ROBODM_PYTHON goto :find
call "%ROBODM_PYTHON%" -c "%RDM_CHECK%" >nul 2>&1 && goto :custom
:find
call python -c "%RDM_CHECK%" >nul 2>&1 && goto :python
call py -3 -c "%RDM_CHECK%" >nul 2>&1 && goto :py
call python3 -c "%RDM_CHECK%" >nul 2>&1 && goto :python3
echo RoboDM needs Python 3.9+, and none of python, py -3 or python3 runs. 1>&2
echo "python3" may be the Microsoft Store placeholder: install Python from python.org, 1>&2
echo or set ROBODM_PYTHON to a working python.exe. 1>&2
exit /b 1

rem Probes use CALL so a .bat/.cmd shim (pyenv-win, or a broken placeholder) returns here instead of
rem ending this script. "%~dp0." is the repo root; the trailing dot stops "\" escaping the closing quote.
:custom
"%ROBODM_PYTHON%" -c "%RDM_BOOT%" "%~dp0." %*
exit /b %ERRORLEVEL%
:python
python -c "%RDM_BOOT%" "%~dp0." %*
exit /b %ERRORLEVEL%
:py
py -3 -c "%RDM_BOOT%" "%~dp0." %*
exit /b %ERRORLEVEL%
:python3
python3 -c "%RDM_BOOT%" "%~dp0." %*
exit /b %ERRORLEVEL%
