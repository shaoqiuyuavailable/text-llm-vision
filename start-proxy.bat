@echo off
set DIR=%USERPROFILE%\.claude\vision-eyes
if not exist "%DIR%\state" echo on> "%DIR%\state"
netstat -ano | findstr ":8787" >nul 2>&1
if %errorlevel% neq 0 (
  cd /d "%DIR%" && start /b python -m uvicorn proxy:app --port 8787
)
