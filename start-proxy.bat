@echo off
rem vision-proxy auto-start script (ASCII only; cmd parses GBK, avoid UTF-8 CJK comments)
set DIR=%USERPROFILE%\.claude\vision-eyes
set PORT=8787

if not exist "%DIR%\state" echo on> "%DIR%\state"

rem ==== 1. already running -> skip (verify via /health, not just port) ====
netstat -ano | findstr ":%PORT%" | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 (
  curl -s --max-time 2 "http://localhost:%PORT%/health" >nul 2>&1
  if %errorlevel% equ 0 (
    echo [vision] proxy already running on :%PORT% (healthy)
  ) else (
    echo [vision] WARN: port :%PORT% in use but /health not responding - check proxy
  )
  exit /b 0
)

rem ==== 2. dependency pre-check (python + uvicorn/fastapi/httpx) ====
python -c "import uvicorn, fastapi, httpx" >nul 2>&1
if %errorlevel% neq 0 (
  echo [vision] FAIL: python or deps (uvicorn/fastapi/httpx) not found in PATH.
  echo [vision]        proxy NOT started. Fix environment then re-open Claude Code.
  exit /b 1
)

rem ==== 3. launch ====
cd /d "%DIR%"
start /b python -m uvicorn proxy:app --port %PORT% >nul 2>&1

rem ==== 4. post-launch health check ====
timeout /t 2 /nobreak >nul 2>&1
curl -s --max-time 3 "http://localhost:%PORT%/health" >nul 2>&1
if %errorlevel% equ 0 (
  echo [vision] proxy started OK on :%PORT%
) else (
  echo [vision] WARN: proxy launched but /health not responding - see vision-proxy.log
)
exit /b 0
