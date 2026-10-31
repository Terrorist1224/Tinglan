@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo 请先双击 安装.cmd 完成环境安装。
 pause
 exit /b 1
)
if not exist "frontend\dist\index.html" (
 echo 网页尚未构建，请先双击 安装.cmd。
 pause
 exit /b 1
)
".venv\Scripts\python.exe" scripts\launch.py
if errorlevel 1 pause
