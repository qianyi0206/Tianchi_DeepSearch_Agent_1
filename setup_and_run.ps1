# 设置错误时停止
$ErrorActionPreference = "Stop"

$PythonVersion = "3.10.11"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$ZipFile = "python-embed.zip"
$ExtractPath = "$PSScriptRoot\python-embed"

Write-Host "=== 开始全自动配置免安装 Python 环境 ===" -ForegroundColor Cyan

# 1. 下载 Python 免安装包
if (-not (Test-Path $ExtractPath)) {
    Write-Host "正在下载 Python $PythonVersion (约 10MB)..." -ForegroundColor Green
    try {
        Invoke-WebRequest -Uri $PythonUrl -OutFile $ZipFile
    } catch {
        Write-Host "下载失败，请检查网络。" -ForegroundColor Red
        exit 1
    }

    # 2. 解压
    Write-Host "正在解压..." -ForegroundColor Green
    Expand-Archive -Path $ZipFile -DestinationPath $ExtractPath -Force
    Remove-Item $ZipFile
} else {
    Write-Host "检测到 Python 环境已存在，跳过下载。" -ForegroundColor Yellow
}

# 3. 配置 Python 以支持 pip
$PthFile = Join-Path $ExtractPath "python310._pth"
if (Test-Path $PthFile) {
    $Content = Get-Content $PthFile
    # 取消 import site 的注释，否则无法安装 pip
    $NewContent = $Content -replace "#import site", "import site"
    Set-Content $PthFile $NewContent
}

# 4. 安装 pip
$PipExe = Join-Path $ExtractPath "Scripts\pip.exe"
$PythonExe = Join-Path $ExtractPath "python.exe"

if (-not (Test-Path $PipExe)) {
    Write-Host "正在安装 pip..." -ForegroundColor Green
    $GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $GetPipFile = "get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipFile
    
    & $PythonExe $GetPipFile --no-warn-script-location
    Remove-Item $GetPipFile
}

# 5. 安装依赖
Write-Host "正在安装项目依赖..." -ForegroundColor Green
& $PythonExe -m pip install -r requirements.txt --no-warn-script-location

# 6. 运行项目
Write-Host "=== 环境配置完成，正在运行项目 ===" -ForegroundColor Cyan
& $PythonExe run_one_eval.py

# 暂停以查看输出
Read-Host "按回车键退出..."
