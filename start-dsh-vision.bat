@echo off
REM dsh-vision: verify then start dsh web with the vision plugin
chcp 65001 >nul
cd /d D:\deepseek-harness

echo [1/2] Verify dsh-vision mount ...
python "F:\code of PY\dsh_vision\scripts\verify_mount.py"
if errorlevel 1 (
  echo Verification FAILED.
  pause
  exit /b 1
)

echo [2/2] Start dsh web with vision plugin ...
pnpm dsh web --patch "F:\code of PY\dsh_vision\cordis.patch.yml"
