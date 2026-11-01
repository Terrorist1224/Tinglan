$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw '请先安装开发环境：scripts/install.ps1 -Dev。' }
& .venv/Scripts/python.exe -c "import pytest, srt"
if ($LASTEXITCODE) { throw '缺少测试依赖，请运行 scripts/install.ps1 -Dev。' }
$logDirectory = Join-Path $PWD ('.tmp/check/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Start-Transcript -Path (Join-Path $logDirectory 'check.log') | Out-Null
try {
    & .venv/Scripts/python.exe scripts/make_fixtures.py
    if ($LASTEXITCODE) { throw '测试媒体生成失败。' }
    & .venv/Scripts/python.exe -m pytest -q
    if ($LASTEXITCODE) { throw '后端回归失败。' }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE) { throw '前端类型检查或构建失败。' }
    & npm.cmd --prefix frontend test
    if ($LASTEXITCODE) { throw '浏览器回归失败。' }
} finally {
    Stop-Transcript | Out-Null
}
