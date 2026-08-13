@echo off
set /p ST=<"%USERPROFILE%\.claude\vision-eyes\state"
if "%ST%"=="off" (echo [vision] OFF) else (echo [vision] ON)
