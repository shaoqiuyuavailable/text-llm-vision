@echo off
setlocal
set "ST="
set /p ST=<"%USERPROFILE%\.claude\vision-eyes\state"
if "%ST%"=="0" goto :off
if "%ST%"=="3" goto :deep
if "%ST%"=="2" goto :std
if "%ST%"=="off" goto :off
if "%ST%"=="deep" goto :deep
if "%ST%"=="standard" goto :std
if "%ST%"=="fast" goto :fast
:fast
echo [vision] fast (1)
exit /b
:std
echo [vision] standard (2)
exit /b
:deep
echo [vision] deep (3)
exit /b
:off
echo [vision] OFF (0)
exit /b
