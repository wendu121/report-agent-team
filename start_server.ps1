# 启动服务层脚本（Windows）
# M6-6 产出：一键启动脚本

$ErrorActionPreference = "Stop"

Write-Host "🚀 启动 Report Agent Team 服务层" -ForegroundColor Green

# 检查 Python 版本
$pythonVersion = python --version 2>&1
Write-Host "Python 版本: $pythonVersion" -ForegroundColor Cyan

# 检查依赖
Write-Host "📦 检查依赖..." -ForegroundColor Cyan
try {
    python -c "import fastapi" 2>$null
} catch {
    Write-Host "安装依赖..." -ForegroundColor Yellow
    pip install -r requirements.txt
}

# 初始化数据库（可选）
Write-Host "🗄️  初始化数据库..." -ForegroundColor Cyan
if ($env:INIT_DATABASE -eq "true") {
    python -c "from server.database import init_database; init_database()"
}

# 启动服务
Write-Host "🌐 启动 FastAPI 服务..." -ForegroundColor Green
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload