@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title DLkit Setup

echo.
echo ============================================================
echo   DLkit Setup
echo ============================================================
echo.

:: ----------------------------------------------------------------
:: 1. Check Python 3.8+
:: ----------------------------------------------------------------
echo [1/5] Checking Python...

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python was not found.
    echo  Please download and install Python 3.11 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: On the first installer screen, tick
    echo  "Add Python to PATH" before clicking Install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PY_VER=%%V
for /f "tokens=1,2 delims=." %%A in ("!PY_VER!") do (
    set PY_MAJ=%%A
    set PY_MIN=%%B
)
if !PY_MAJ! LSS 3 (
    echo  ERROR: Python 3.8+ is required. Found !PY_VER!.
    pause
    exit /b 1
)
if !PY_MAJ! EQU 3 if !PY_MIN! LSS 8 (
    echo  ERROR: Python 3.8+ is required. Found !PY_VER!.
    pause
    exit /b 1
)
echo  OK: Python !PY_VER!

:: ----------------------------------------------------------------
:: 2. Create virtual environment
:: ----------------------------------------------------------------
echo.
echo [2/5] Creating virtual environment...

if exist venv\ (
    echo  Existing venv found - skipping creation.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Created venv\
)

:: ----------------------------------------------------------------
:: 3. Install Python dependencies
:: ----------------------------------------------------------------
echo.
echo [3/5] Installing Python packages (Flask, yt-dlp)...

venv\Scripts\python.exe -m pip install --upgrade pip --quiet
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  OK: packages installed.

:: ----------------------------------------------------------------
:: 4. Download ffmpeg
:: ----------------------------------------------------------------
echo.
echo [4/5] Checking for ffmpeg...

if exist ffmpeg.exe (
    echo  OK: ffmpeg.exe already present.
    goto ffmpeg_done
)

echo  Downloading ffmpeg via winget...
winget --version >nul 2>&1
if errorlevel 1 goto ffmpeg_manual

winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements

:: Give PATH a moment to update then locate and copy binaries here
for /f "delims=" %%P in ('where ffmpeg 2^>nul') do set FFMPEG_PATH=%%P
if defined FFMPEG_PATH (
    copy "!FFMPEG_PATH!" ffmpeg.exe >nul
    for /f "delims=" %%P in ('where ffprobe 2^>nul') do copy "%%P" ffprobe.exe >nul
    echo  OK: ffmpeg copied to project folder.
    goto ffmpeg_done
)

:: winget installed but PATH not refreshed yet - try common install location
set FFMPEG_GUESS=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe
for /r "%FFMPEG_GUESS%" %%F in (ffmpeg.exe) do (
    copy "%%F" ffmpeg.exe >nul
    for %%D in ("%%~dpF") do copy "%%~dpFffprobe.exe" ffprobe.exe >nul
    echo  OK: ffmpeg copied to project folder.
    goto ffmpeg_done
)

:ffmpeg_manual
echo.
echo  Could not install ffmpeg automatically.
echo  Run this command in PowerShell to install and copy it here:
echo.
echo    winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements ; Copy-Item (Get-Command ffmpeg).Source .; Copy-Item (Get-Command ffprobe).Source .
echo.
echo  Then run setup.bat again.
echo.
pause
exit /b 1

:ffmpeg_done

:: ----------------------------------------------------------------
:: 5. Create desktop shortcut
:: ----------------------------------------------------------------
echo.
echo [5/5] Creating desktop shortcut...

set SHORTCUT=%USERPROFILE%\Desktop\DLkit.lnk
set TARGET=%~dp0start.bat

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut('%SHORTCUT%'); ^
   $sc.TargetPath = '%TARGET%'; ^
   $sc.WorkingDirectory = '%~dp0'; ^
   $sc.Description = 'Start DLkit'; ^
   $sc.Save()" >nul 2>&1

if exist "%SHORTCUT%" (
    echo  OK: Shortcut created on Desktop.
) else (
    echo  Note: Could not create shortcut. Run start.bat directly.
)

:: ----------------------------------------------------------------
:: Done
:: ----------------------------------------------------------------
echo.
echo ============================================================
echo   Setup complete!
echo   Double-click "DLkit" on your Desktop  - or run start.bat
echo ============================================================
echo.
pause
