@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
 echo 安装失败，详情见 .tmp\setup 下的日志。
 pause
 exit /b 1
)
pause
