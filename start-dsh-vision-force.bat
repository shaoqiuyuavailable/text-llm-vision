@echo off
REM dsh-vision: kill existing dsh on :3080, verify, then start with vision plugin
chcp 65001 >nul
cd /d D:\deepseek-harness

echo [0/3] Free port 3080 if occupied ...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('Kill PID ' + $_.OwningProcess); Stop-Process -Id $_.OwningProcess -Force }"

echo [1/3] Verify dsh-vision mount ...
python "F:\code of PY\dsh_vision\scripts\verify_mount.py"
if errorlevel 1 (
  echo Verification FAILED.
  pause
  exit /b 1
)

echo [2/3] Start dsh web with vision plugin ...
pnpm dsh web --patch "F:\code of PY\dsh_vision\cordis.patch.yml"
