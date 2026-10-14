@echo off
REM Double-click starter: swapped face straight to Teams / Meet / Zoom.
REM Drag & Drop: You can also drag and drop any picture file onto this .bat file to swap with it!
cd /d %~dp0

set "REF_IMG=%~1"
if "%REF_IMG%"=="" set "REF_IMG=C:\Users\Besta\Pictures\wixlans\image.png"

echo Starting OmniLive with face: "%REF_IMG%"
python pc_peer.py --reference "%REF_IMG%" --preview
echo.
echo Stopped. You can close this window.
pause

