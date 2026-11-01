param([switch]$Dev)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$projectRoot = [System.IO.Path]::GetFullPath($PWD.Path)
$rootPrefix = $projectRoot + [System.IO.Path]::DirectorySeparatorChar
$running = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.Name -match '^(python(w)?|ffmpeg|ffprobe)\.exe$' -and $_.ExecutablePath -and
    $_.ExecutablePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}
if ($running) { throw '主环境正在使用，请停止听澜服务、模型处理和测试后再安装。' }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '请先安装 uv。' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw '请先安装 Node.js LTS。' }
$nodeVersionText = & node --version
if ($LASTEXITCODE -or [version]($nodeVersionText.TrimStart('v')) -lt [version]'22.12') { throw '需要 Node.js 22.12 或更新的受支持 LTS 版本。' }
$hasEnvironment = Test-Path '.venv/Scripts/python.exe'
$minimumSpace = if ($hasEnvironment) { 4GB } else { 16GB }
$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($projectRoot))
if ($drive.AvailableFreeSpace -lt $minimumSpace) { throw '磁盘空间不足，现有环境更新至少预留 4 GiB，新安装至少预留 16 GiB。' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:UV_CACHE_DIR = Join-Path $projectRoot '.tmp/uv-cache'
$env:npm_config_cache = Join-Path $projectRoot '.tmp/npm-cache'
$env:UV_HTTP_TIMEOUT = '180'
$env:UV_PYTHON_DOWNLOADS = 'never'
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '-' + $PID
$setupDirectory = Join-Path $projectRoot ('.tmp/setup/' + $runId)
New-Item -ItemType Directory -Force -Path $setupDirectory, 'tools', 'models' | Out-Null
Start-Transcript -Path (Join-Path $setupDirectory 'install.log') | Out-Null
try {
    $proxySettings = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($proxySettings.ProxyEnable -eq 1 -and $proxySettings.ProxyServer -match '^127\.0\.0\.1:\d+$') {
        $env:HTTPS_PROXY = 'http://' + $proxySettings.ProxyServer
        $env:HTTP_PROXY = $env:HTTPS_PROXY
    }
    if (-not $hasEnvironment) {
        & uv venv --python 3.12 .venv
        if ($LASTEXITCODE) { throw '创建 Python 3.12 环境失败，请先安装 64 位 Python 3.12。' }
    }
    & .venv/Scripts/python.exe -c "import sys; assert sys.version_info[:2] == (3,12) and sys.maxsize > 2**32, 'Requires 64-bit Python 3.12'"
    if ($LASTEXITCODE) { throw '主环境需要 64 位 Python 3.12。' }
    & uv pip freeze --python .venv/Scripts/python.exe | Set-Content -LiteralPath (Join-Path $setupDirectory 'requirements-before.txt') -Encoding utf8
    if ($LASTEXITCODE) { throw '无法保存安装前依赖清单。' }
    $requirements = @('requirements/run.lock.txt')
    if ($Dev) { $requirements += 'requirements/dev.lock.txt' }
    & uv pip sync --python .venv/Scripts/python.exe @requirements --dry-run --index https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-first-match
    if ($LASTEXITCODE) { throw '依赖同步预检失败。' }
    & uv pip sync --python .venv/Scripts/python.exe @requirements --index https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-first-match
    if ($LASTEXITCODE) { throw '依赖同步失败，版本记录在本次安装日志目录。' }
    if (-not (Test-Path 'tools/ffmpeg.exe') -or -not (Test-Path 'tools/ffprobe.exe')) {
        $archive = Join-Path $setupDirectory 'ffmpeg.partial.zip'
        Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive -TimeoutSec 180
        & .venv/Scripts/python.exe scripts/setup_tools.py $archive tools
        if ($LASTEXITCODE) { throw 'FFmpeg 校验或安装失败。' }
    }
    $toolVersion = @(& tools/ffmpeg.exe -version)
    if ($LASTEXITCODE) { throw 'FFmpeg 不能运行。' }
    $toolVersion | Select-Object -First 1
    $toolVersion = @(& tools/ffprobe.exe -version)
    if ($LASTEXITCODE) { throw 'FFprobe 不能运行。' }
    $toolVersion | Select-Object -First 1
    & npm.cmd ci --prefix frontend
    if ($LASTEXITCODE) { throw '前端依赖安装失败。' }
    $stagedDist = Join-Path $setupDirectory 'frontend-dist'
    & npm.cmd run build --prefix frontend -- --outDir $stagedDist
    if ($LASTEXITCODE -or -not (Test-Path (Join-Path $stagedDist 'index.html'))) { throw '前端构建失败，保留上次可用产物。' }
    $currentDist = Join-Path $projectRoot 'frontend/dist'
    $previousDist = Join-Path $setupDirectory 'frontend-dist-before'
    foreach ($target in @($stagedDist, $currentDist, $previousDist)) {
        $absolute = [System.IO.Path]::GetFullPath($target)
        if (-not $absolute.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw '构建路径超出项目目录。' }
    }
    if (Test-Path -LiteralPath $currentDist) { Move-Item -LiteralPath $currentDist -Destination $previousDist }
    try {
        Move-Item -LiteralPath $stagedDist -Destination $currentDist
    } catch {
        if (Test-Path -LiteralPath $previousDist) { Move-Item -LiteralPath $previousDist -Destination $currentDist }
        throw
    }
    Write-Host '安装完成。双击 启动.cmd；首次使用时在“全局 API 设置”准备模型。'
} finally {
    Stop-Transcript | Out-Null
}
