@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  restart_claude.bat — 重启 Claude Code + 确保 vision MCP 挂上
REM
REM  用法: 在【外部】终端或双击运行（会 taskkill 当前 claude.exe，
REM        请在别处保存好当前会话内容）
REM  流程: 1) 确保 MCP 注册（vision -> mcp_server.py, remove->add 幂等）
REM        2) 确保代理运行（新代码, 已在跑则跳过）
REM        3) 杀旧 claude.exe + 残留 node mcp-vision.js, 重启 claude
REM ============================================================
set "DEPLOY=%USERPROFILE%\.claude\vision-eyes"

echo.
echo [1/3] ensure vision MCP attached (python mcp_server.py)
python "%DEPLOY%\install.py" --mcp claude
if errorlevel 1 echo   [WARN] MCP register had errors, check above

echo.
echo [2/3] ensure proxy running (new code)
python "%DEPLOY%\start_proxy.py"

echo.
echo [3/3] restart Claude Code
echo   killing old claude.exe and stale node mcp-vision.js ...
taskkill /F /IM claude.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*mcp-vision.js*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo   relaunching Claude Code ...
cd /d "%USERPROFILE%"
claude
