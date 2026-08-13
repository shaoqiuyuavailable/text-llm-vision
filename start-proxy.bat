@echo off
rem vision-proxy auto-start shell: delegates to start_proxy.py (all logic in Python)
rem keeps cmd parsing dead-simple (no quoting/errorlevel pitfalls)
set DIR=%USERPROFILE%\.claude\vision-eyes
if not exist "%DIR%\state" echo on> "%DIR%\state"
python "%DIR%\start_proxy.py"
