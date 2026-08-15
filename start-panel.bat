@echo off
REM dsh-vision control panel
chcp 65001 >nul
python "F:\code of PY\dsh_vision\panel\server.py" --port 8790
pause
