@echo off
REM Double-click starter: swapped face straight to Teams / Meet / Zoom / OBS.
REM Drag & Drop: You can drag and drop any picture file onto this .bat file to swap with it!
cd /d %~dp0

set "REF_IMG=%~1"
if "%REF_IMG%"=="" set "REF_IMG=C:\Users\Besta\Pictures\wixlans\image.png"

echo ============================================================
echo   OmniLive Face-Swapped Virtual Camera
echo ============================================================
echo Face identity image: "%REF_IMG%"
echo.
echo Select Camera Source:
echo   [0] Laptop Webcam (Default)
echo   [1] Phone Camera / External USB (DroidCam, Iriun, etc.)
echo.
set /p "CAM_NUM=Enter camera number [0 or 1, press Enter for 0]: "
if "%CAM_NUM%"=="" set "CAM_NUM=0"

echo.
echo Starting OmniLive on Camera %CAM_NUM%...
python pc_peer.py --reference "%REF_IMG%" --camera %CAM_NUM% --preview
echo.
echo Stopped. You can close this window.
pause
