@echo off
REM Double-click starter: swapped face straight to Teams / Meet / Zoom.
REM Keep this window OPEN while you meet. Close it to stop (billing stops too).
cd /d %~dp0
python pc_peer.py --reference "C:\Users\Besta\Pictures\wixlans\image.png"
echo.
echo Stopped. You can close this window.
pause
