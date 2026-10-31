$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path '.tmp/setup','tools','models' | Out-Null
$env:UV_CACHE_DIR = "$PWD/.tmp/uv-cache"
$env:npm_config_cache = "$PWD/.tmp/npm-cache"
$env:UV_HTTP_TIMEOUT = '180'
$proxySettings = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
if ($proxySettings.ProxyEnable -eq 1 -and $proxySettings.ProxyServer -match '^127\.0\.0\.1:\d+$') {
    $env:HTTPS_PROXY = 'http://' + $proxySettings.ProxyServer
    $env:HTTP_PROXY = $env:HTTPS_PROXY
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '请先安装 uv，再运行此脚本。' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw '请先安装 Node.js LTS，再运行此脚本。' }
$env:UV_PYTHON_DOWNLOADS = 'never'
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    uv venv --python 3.12 .venv
    if ($LASTEXITCODE) { throw '创建 Python 3.12 环境失败，请确认已安装 Python 3.12。' }
}
uv pip install --python .venv/Scripts/python.exe -r requirements.lock.txt --index https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-first-match
if ($LASTEXITCODE) { throw '主模型环境安装失败。' }
if (-not (Test-Path tools/ffprobe.exe) -or -not (Test-Path tools/ffmpeg.exe)) {
    Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile .tmp/setup/ffmpeg.zip
    .venv/Scripts/python.exe -c "import zipfile,pathlib;z=zipfile.ZipFile('.tmp/setup/ffmpeg.zip');[(pathlib.Path('tools')/pathlib.Path(n).name).write_bytes(z.read(n)) for n in z.namelist() if n.endswith(('/bin/ffprobe.exe','/bin/ffmpeg.exe'))]"
    if ($LASTEXITCODE) { throw 'FFmpeg 解压失败。' }
}
npm.cmd ci --prefix frontend
if ($LASTEXITCODE) { throw '网页依赖安装失败。' }
npm.cmd run build --prefix frontend
if ($LASTEXITCODE) { throw '网页构建失败。' }
Write-Host '环境安装完成。双击 启动.cmd，然后在“设置与模型”中下载Whisper large-v3。'
