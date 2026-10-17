@echo off
rem Pitchframe's launcher. Double-click this file.
rem
rem It exists so that opening the overlay does not need a terminal, a bookmark that works
rem only while something else is running, or anyone to start it for you:
rem
rem   * it finds a Python 3.13 (this checkout's virtual environment first),
rem   * asks the overlay whether something is already serving this checkout,
rem   * and then either opens the catalogue in your browser or becomes the server itself.
rem
rem The command underneath is `python -m pitchframe --overlay --open`. Everything the
rem launcher decides is decided there, where it can be tested; this file only finds an
rem interpreter and keeps the window open long enough to read an error.
rem
rem While this window is the server, leave it open and close it to stop. If it only opened
rem your browser, it exits at once and the server it found keeps running.

setlocal EnableExtensions
title Pitchframe overlay
cd /d "%~dp0"

set "PORT=8000"
set "PY="

rem The virtual environment this repository's README tells you to create, if it is there.
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"

rem Otherwise the launcher PyPI ships with Python on Windows, then whatever `python` means.
if not defined PY (
  py -3.13 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3.13"
)
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY goto :nopython

echo Pitchframe. Opening http://127.0.0.1:%PORT%/ in your browser.
echo.
"%PY%" -m pitchframe --overlay --open --port %PORT%
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
  echo.
  echo Pitchframe stopped with exit code %RESULT%. The reason is above.
  echo.
  pause
)
exit /b %RESULT%

:nopython
echo Pitchframe needs Python 3.13, and there is none on this machine's PATH.
echo.
echo   1. install it from https://www.python.org/downloads/
echo   2. then, in this folder:  python -m pip install -r requirements.txt
echo   3. then run this file again
echo.
pause
exit /b 2
