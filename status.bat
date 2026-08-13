@echo off
setlocal
set DIR=%USERPROFILE%\.claude\vision-eyes
set "ST="
set /p ST=<"%DIR%\state"
if "%ST%"=="0" goto :off
if "%ST%"=="3" goto :deep
if "%ST%"=="2" goto :std
if "%ST%"=="off" goto :off
if "%ST%"=="deep" goto :deep
if "%ST%"=="standard" goto :std
if "%ST%"=="fast" goto :fast
:fast
for /f "delims=" %%P in ('python "%DIR%\read_port.py" 2^>nul') do set PORT=%%P
if not defined PORT set PORT=8787
echo [vision] fast (1) :%PORT%
exit /b 0
:std
for /f "delims=" %%P in ('python "%DIR%\read_port.py" 2^>nul') do set PORT=%%P
if not defined PORT set PORT=8787
echo [vision] standard (2) :%PORT%
exit /b 0
:deep
for /f "delims=" %%P in ('python "%DIR%\read_port.py" 2^>nul') do set PORT=%%P
if not defined PORT set PORT=8787
echo [vision] deep (3) :%PORT%
exit /b 0
:off
for /f "delims=" %%P in ('python "%DIR%\read_port.py" 2^>nul') do set PORT=%%P
if not defined PORT set PORT=8787
echo [vision] OFF (0) :%PORT%
exit /b 0
